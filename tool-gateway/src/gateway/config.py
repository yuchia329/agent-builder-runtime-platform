"""Settings from environment variables (docs/architecture.md → Configuration)."""

import os
from dataclasses import dataclass
from pathlib import Path

REPO_CONTRACTS = Path(__file__).resolve().parents[3] / "contracts"
CONNECTORS_FOLDER = Path(__file__).resolve().parents[2] / "connectors"


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
    env: tuple[tuple[str, str], ...] = ()  # for settings an allowlist names (GITHUB_REPOS, ...)

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
            env=tuple(sorted(env.items())),
        )
