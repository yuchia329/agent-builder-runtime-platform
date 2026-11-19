"""`uv run gateway`: serve the tool gateway with the connectors in CONNECTORS."""

import logging

import uvicorn

from gateway.adapters.control_plane_http import HttpControlPlane
from gateway.adapters.token_store_memory import MemoryTokenStore
from gateway.app import create_app
from gateway.approvals import ApprovalCheck
from gateway.auth.run_token import RunTokenVerifier
from gateway.config import Settings
from gateway.connectors.fake import FakeConnector
from gateway.contracts import Contracts
from gateway.credentials import Credentials
from gateway.ports.token_store import TokenStore
from gateway.seed import seed_from_env
from gateway.tools.executor import ConnectorClient
from gateway.tools.openapi_to_mcp import ConnectorError, load_connector
from gateway.tools.registry import Registry

log = logging.getLogger("gateway")


def connectors(settings: Settings, contracts: Contracts, store: TokenStore) -> list:
    loaded = []
    credentials = Credentials(store)  # one installation-token cache for all connectors
    for name in settings.connectors:
        if name == "fake":
            loaded.append(FakeConnector())
            continue
        folder = settings.connectors_dir / name
        if not (folder / "allowlist.yaml").is_file():
            raise ConnectorError(f"Unknown connector in CONNECTORS: {name} (no {folder})")
        definition = load_connector(folder, contracts, dict(settings.env))
        loaded.append(ConnectorClient(definition, credentials))
    return loaded


def build(settings: Settings, store: TokenStore | None = None):
    contracts = Contracts(settings.contracts_dir)
    if store is None:
        store = MemoryTokenStore()
        for seeded in seed_from_env(store, dict(settings.env)):
            log.info("Credential from the environment: %s", seeded)
    registry = Registry(connectors(settings, contracts, store))
    verifier = RunTokenVerifier(settings.run_token_public_key, contracts)
    log.info(
        "Tool gateway: connectors %s, run token key id %s",
        ",".join(settings.connectors) or "none",
        verifier.key_id,
    )
    if settings.control_plane_url:
        approvals = ApprovalCheck(HttpControlPlane(settings.control_plane_url))
    else:
        approvals = ApprovalCheck(None)
        log.warning("CONTROL_PLANE_URL is not set: tools that need approval will be refused")
    return create_app(registry, verifier, contracts, approvals)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = Settings.from_env()
    uvicorn.run(build(settings), host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
