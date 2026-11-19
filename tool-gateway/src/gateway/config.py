"""Settings from environment variables (docs/architecture.md → Configuration)."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

REPO_CONTRACTS = Path(__file__).resolve().parents[3] / "contracts"
CONNECTORS_FOLDER = Path(__file__).resolve().parents[2] / "connectors"


# A connector's base URL may be overridden only to this machine (evals and tests point a real
# connector at a simulator): the override receives the connector's credentials.
LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})

OAUTH = ("GITHUB_OAUTH_CLIENT_ID", "GITHUB_OAUTH_CLIENT_SECRET", "OAUTH_STATE_SECRET")
MIN_STATE_SECRET = 32


def connector_base_url(name: str, env: dict[str, str]) -> tuple[str, str] | None:
    """The `CONNECTOR_<NAME>_BASE_URL` override for a connector, as (setting, URL), or None.

    Only http(s) URLs on this machine are accepted: the connector's credentials (a GitHub App
    token request, for example) go to that address.
    """
    setting = f"CONNECTOR_{name.upper().replace('-', '_')}_BASE_URL"
    raw = env.get(setting, "").strip()
    if not raw:
        return None
    try:
        url = urlsplit(raw)
        host = url.hostname
        url.port  # noqa: B018 - raises on a bad port
    except ValueError:
        raise ValueError(f"{setting} is not a valid URL") from None
    if url.scheme not in ("http", "https") or not host:
        raise ValueError(f"{setting} must be an http:// or https:// URL")
    if url.username or url.password or url.query or url.fragment:
        raise ValueError(f"{setting} must not hold a user, query or fragment")
    if host not in LOOPBACK:
        raise ValueError(
            f"{setting} must point at this machine (127.0.0.1, localhost or ::1), not {host}: "
            "it would receive the connector's credentials"
        )
    return setting, raw.rstrip("/")


@dataclass(frozen=True)
class OAuthSettings:
    """User login for acts_as owner through the GitHub App (ADR 0015)."""

    client_id: str
    client_secret: str = field(repr=False)
    state_secret: str = field(repr=False)
    public_url: str = "http://localhost:8090"  # where browsers reach the gateway

    @classmethod
    def from_env(cls, env: dict[str, str]) -> "OAuthSettings | None":
        values = {name: env.get(name, "").strip() for name in OAUTH}
        if not any(values.values()):
            return None
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise ValueError(f"User login settings incomplete: set {', '.join(missing)}")
        if len(values["OAUTH_STATE_SECRET"]) < MIN_STATE_SECRET:
            raise ValueError(
                f"OAUTH_STATE_SECRET needs at least {MIN_STATE_SECRET} characters "
                "(openssl rand -hex 32)"
            )
        public = env.get("GATEWAY_PUBLIC_URL", "").strip().rstrip("/") or cls.public_url
        if not public.startswith(("http://", "https://")):
            raise ValueError("GATEWAY_PUBLIC_URL must start with http:// or https://")
        return cls(*values.values(), public_url=public)


@dataclass(frozen=True)
class Settings:
    run_token_public_key: str
    connectors: tuple[str, ...]
    token_store: str = "memory"  # or "ssm" (ADR 0018)
    token_store_path: str = ""  # TOKEN_STORE_SSM_PATH, for ssm
    host: str = "127.0.0.1"
    port: int = 8090
    contracts_dir: Path = REPO_CONTRACTS
    connectors_dir: Path = CONNECTORS_FOLDER
    control_plane_url: str = ""  # for the approval check; empty: approval tools refused
    control_plane_sigv4: bool = False  # sign calls to it with SigV4 (AWS, CONTROL_PLANE_SIGV4)
    region: str = ""  # AWS_REGION, for SigV4
    oauth: OAuthSettings | None = None  # user login for acts_as owner; None: owner runs refused
    # For settings an allowlist names (GITHUB_REPOS, ...); holds secrets, so never shown.
    env: tuple[tuple[str, str], ...] = field(default=(), repr=False)

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Settings":
        env = dict(os.environ if env is None else env)
        key = env.get("RUN_TOKEN_PUBLIC_KEY", "").strip()
        if not key:
            raise ValueError(
                "RUN_TOKEN_PUBLIC_KEY is required (the control plane's run token key; "
                "on AWS RUN_TOKEN_KMS_KEY_ID)"
            )
        store = env.get("TOKEN_STORE", "memory").strip()
        store_path = env.get("TOKEN_STORE_SSM_PATH", "").strip()
        if store not in ("memory", "ssm"):
            raise ValueError(f"TOKEN_STORE must be memory or ssm, not {store!r}")
        if store == "ssm" and not store_path.startswith("/"):
            raise ValueError("TOKEN_STORE=ssm needs TOKEN_STORE_SSM_PATH, a path starting with /")
        sigv4 = env.get("CONTROL_PLANE_SIGV4", "").strip().lower()
        if sigv4 not in ("", "true", "false"):
            raise ValueError("CONTROL_PLANE_SIGV4 must be true or false")
        region = env.get("AWS_REGION", "").strip()
        if sigv4 == "true" and not region:
            raise ValueError("CONTROL_PLANE_SIGV4=true needs AWS_REGION")
        return cls(
            run_token_public_key=key,
            connectors=tuple(c.strip() for c in env.get("CONNECTORS", "").split(",") if c.strip()),
            token_store=store,
            token_store_path=store_path,
            control_plane_sigv4=sigv4 == "true",
            region=region,
            host=env.get("GATEWAY_HOST", "127.0.0.1"),
            port=int(env.get("GATEWAY_PORT", "8090")),
            contracts_dir=Path(env.get("CONTRACTS_DIR", str(REPO_CONTRACTS))),
            connectors_dir=Path(env.get("CONNECTORS_DIR", str(CONNECTORS_FOLDER))),
            control_plane_url=env.get("CONTROL_PLANE_URL", "").strip(),
            oauth=OAuthSettings.from_env(env),
            env=tuple(sorted(env.items())),
        )
