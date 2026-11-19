"""`uv run gateway`: serve the tool gateway with the connectors in CONNECTORS."""

import importlib.util
import logging
from collections.abc import Callable
from pathlib import Path

import uvicorn

from gateway.adapters.token_store_memory import MemoryTokenStore
from gateway.app import create_app
from gateway.auth.run_token import RunTokenVerifier
from gateway.config import Settings
from gateway.connectors.fake import FakeConnector
from gateway.contracts import Contracts
from gateway.credentials import Credentials
from gateway.ports.token_store import TokenStore
from gateway.tools.executor import ConnectorClient
from gateway.tools.openapi_to_mcp import ConnectorError, load_connector
from gateway.tools.registry import Registry

log = logging.getLogger("gateway")


def load_hooks(folder: Path) -> dict[str, Callable]:
    """`HOOKS` from the connector folder's hooks.py, if it has one (ADR 0014)."""
    path = folder / "hooks.py"
    if not path.is_file():
        return {}
    spec = importlib.util.spec_from_file_location(f"gateway_hooks_{folder.name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return dict(getattr(module, "HOOKS", {}))


def connectors(settings: Settings, contracts: Contracts, store: TokenStore) -> list:
    loaded = []
    for name in settings.connectors:
        if name == "fake":
            loaded.append(FakeConnector())
            continue
        folder = settings.connectors_dir / name
        if not (folder / "allowlist.yaml").is_file():
            raise ConnectorError(f"Unknown connector in CONNECTORS: {name} (no {folder})")
        definition = load_connector(folder, contracts, dict(settings.env), load_hooks(folder))
        loaded.append(ConnectorClient(definition, Credentials(store)))
    return loaded


def build(settings: Settings, store: TokenStore | None = None):
    contracts = Contracts(settings.contracts_dir)
    store = store or MemoryTokenStore()
    registry = Registry(connectors(settings, contracts, store))
    verifier = RunTokenVerifier(settings.run_token_public_key, contracts)
    log.info(
        "Tool gateway: connectors %s, run token key id %s",
        ",".join(settings.connectors) or "none",
        verifier.key_id,
    )
    return create_app(registry, verifier, contracts)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = Settings.from_env()
    uvicorn.run(build(settings), host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
