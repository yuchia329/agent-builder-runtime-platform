"""`uv run gateway`: serve the tool gateway with the connectors in CONNECTORS."""

import logging
import os

import boto3
import uvicorn

from gateway.adapters.control_plane_http import HttpControlPlane
from gateway.adapters.token_store_memory import MemoryTokenStore
from gateway.adapters.token_store_ssm import SsmTokenStore
from gateway.app import create_app
from gateway.approvals import ApprovalCheck
from gateway.auth.run_token import RunTokenVerifier
from gateway.aws import aws_env, default_signer
from gateway.config import Settings, connector_base_url
from gateway.connectors.fake import FakeConnector
from gateway.contracts import Contracts
from gateway.credentials import Credentials
from gateway.oauth.github import GitHubOAuth
from gateway.oauth.logins import OAuthLogins
from gateway.oauth.routes import oauth_app
from gateway.oauth.signing import Signer
from gateway.ports.token_store import TokenStore
from gateway.seed import seed_from_env
from gateway.tools.executor import ConnectorClient
from gateway.tools.openapi_to_mcp import ConnectorError, load_connector
from gateway.tools.registry import Registry
from gateway.tracing import Tracing, build_tracing

log = logging.getLogger("gateway")


def connectors(
    settings: Settings,
    contracts: Contracts,
    credentials: Credentials,
    tracing: Tracing | None = None,
) -> list:
    loaded = []
    for name in settings.connectors:
        if name == "fake":
            loaded.append(FakeConnector())
            continue
        folder = settings.connectors_dir / name
        if not (folder / "allowlist.yaml").is_file():
            raise ConnectorError(f"Unknown connector in CONNECTORS: {name} (no {folder})")
        env = dict(settings.env)
        definition = load_connector(folder, contracts, env)
        base_url = None
        if override := connector_base_url(name, env):
            setting, base_url = override
            log.warning(
                "Connector %s calls %s instead of %s (%s; evals and tests only)",
                name,
                base_url,
                definition.base_url,
                setting,
            )
        loaded.append(ConnectorClient(definition, credentials, base_url=base_url, tracing=tracing))
    return loaded


def build(settings: Settings, store: TokenStore | None = None):
    contracts = Contracts(settings.contracts_dir)
    if store is None and settings.token_store == "ssm":
        store = SsmTokenStore(boto3.client("ssm"), settings.token_store_path)
        log.info("Credentials in SSM under %s", settings.token_store_path)
    if store is None:
        store = MemoryTokenStore()
        for seeded in seed_from_env(store, dict(settings.env)):
            log.info("Credential from the environment: %s", seeded)
    logins = user_logins(settings, store)
    # One Credentials: one installation-token cache and one refresh lock for all connectors.
    credentials = Credentials(store, logins=logins)
    tracing = build_tracing(settings.tracing_exporter)
    registry = Registry(connectors(settings, contracts, credentials, tracing))
    verifier = RunTokenVerifier(settings.run_token_public_key, contracts)
    log.info(
        "Tool gateway: connectors %s, run token key id %s",
        ",".join(settings.connectors) or "none",
        verifier.key_id,
    )
    if settings.control_plane_url:
        signer = default_signer(settings.region) if settings.control_plane_sigv4 else None
        approvals = ApprovalCheck(HttpControlPlane(settings.control_plane_url, signer=signer))
    else:
        approvals = ApprovalCheck(None)
        log.warning("CONTROL_PLANE_URL is not set: tools that need approval will be refused")
    oauth = None
    if settings.oauth:
        secure = settings.oauth.public_url.startswith("https://")
        oauth = oauth_app(logins, secure_cookie=secure)
        log.info("User login: %s/oauth/github/start", settings.oauth.public_url)
    else:
        log.warning("GITHUB_OAUTH_* is not set: acts_as owner runs will be refused")
    return create_app(registry, verifier, contracts, approvals, oauth, tracing)


def user_logins(settings: Settings, store: TokenStore) -> dict[str, OAuthLogins]:
    if settings.oauth is None:
        return {}
    github = GitHubOAuth(settings.oauth.client_id, settings.oauth.client_secret)
    signer = Signer(settings.oauth.state_secret)
    return {"github": OAuthLogins(store, github, signer, settings.oauth.public_url)}


class HideLoginQueries(logging.Filter):
    """uvicorn's request log prints the full URL; under /oauth/ its query holds a login ticket,
    an OAuth code and state. Keep the path, drop the query."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) == 5 and isinstance(args[2], str):
            path, _, query = args[2].partition("?")
            if query and path.startswith("/oauth/"):
                record.args = (*args[:2], f"{path}?<hidden>", *args[3:])
        return True


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("uvicorn.access").addFilter(HideLoginQueries())
    settings = Settings.from_env(aws_env(dict(os.environ)))
    uvicorn.run(build(settings), host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
