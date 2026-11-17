from typing import Protocol

from worker.core.types import RunRecord, RunUpdate


class ControlPlane(Protocol):
    """The control plane's internal run API (contracts/internal-api.openapi.yaml).

    Adapters: in-memory (tests), HTTP with SigV4 (next task). Contract: tests/contract.
    """

    def get_run(self, run_id: str) -> RunRecord:
        """Raises RunNotFound."""
        ...

    def update_run(self, run_id: str, update: RunUpdate) -> None:
        """Raises RunNotFound, or RunConflict if the status change is not allowed."""
        ...
