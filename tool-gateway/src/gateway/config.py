"""Settings from environment variables (docs/architecture.md → Configuration)."""

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_CONTRACTS = Path(__file__).resolve().parents[3] / "contracts"
CONNECTORS_FOLDER = Path(__file__).resolve().parents[2] / "connectors"


OAUTH = ("GITHUB_OAUTH_CLIENT_ID", "GITHUB_OAUTH_CLIENT_SECRET", "OAUTH_STATE_SECRET")
MIN_STATE_SECRET = 32


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
    token_store: str = "memory"
    host: str = "127.0.0.1"
    port: int = 8090
    contracts_dir: Path = REPO_CONTRACTS
    connectors_dir: Path = CONNECTORS_FOLDER
    control_plane_url: str = ""  # for the approval check; empty: approval tools refused
    oauth: OAuthSettings | None = None  # user login for acts_as owner; None: owner runs refused
    # For settings an allowlist names (GITHUB_REPOS, ...); holds secrets, so never shown.
    env: tuple[tuple[str, str], ...] = field(default=(), repr=False)

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Settings":
        env = dict(os.environ if env is None else env)
        key = env.get("RUN_TOKEN_PUBLIC_KEY", "").strip()
        if not key:
            raise ValueError("RUN_TOKEN_PUBLIC_KEY is required (the control plane's run token key)")
        store = env.get("TOKEN_STORE", "memory")
        if store != "memory":
            raise ValueError(f"TOKEN_STORE={store!r} is not supported yet (memory; ssm in M6)")
        return cls(
            run_token_public_key=key,
            connectors=tuple(c.strip() for c in env.get("CONNECTORS", "").split(",") if c.strip()),
            token_store=store,
            host=env.get("GATEWAY_HOST", "127.0.0.1"),
            port=int(env.get("GATEWAY_PORT", "8090")),
            contracts_dir=Path(env.get("CONTRACTS_DIR", str(REPO_CONTRACTS))),
            connectors_dir=Path(env.get("CONNECTORS_DIR", str(CONNECTORS_FOLDER))),
            control_plane_url=env.get("CONTROL_PLANE_URL", "").strip(),
            oauth=OAuthSettings.from_env(env),
            env=tuple(sorted(env.items())),
        )
