"""`uv run gateway`: serve the tool gateway with the connectors in CONNECTORS."""

import logging

import uvicorn

from gateway.adapters.token_store_memory import MemoryTokenStore
from gateway.app import create_app
from gateway.auth.run_token import RunTokenVerifier
from gateway.config import Settings
from gateway.connectors.fake import FakeConnector
from gateway.contracts import Contracts
from gateway.tools.registry import Registry

CONNECTORS = {"fake": lambda store: FakeConnector()}


def build(settings: Settings):
    contracts = Contracts(settings.contracts_dir)
    store = MemoryTokenStore()
    unknown = [c for c in settings.connectors if c not in CONNECTORS]
    if unknown:
        raise ValueError(f"Unknown connectors in CONNECTORS: {', '.join(unknown)}")
    registry = Registry(CONNECTORS[name](store) for name in settings.connectors)
    verifier = RunTokenVerifier(settings.run_token_public_key, contracts)
    logging.getLogger("gateway").info(
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
