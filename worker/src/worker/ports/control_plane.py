from typing import Protocol

from worker.core.types import RunRecord, RunStatus, RunUpdate


class ControlPlane(Protocol):
    """The control plane's internal run API (contracts/internal-api.openapi.yaml).

    Adapters: in-memory (tests), HTTP with SigV4. Contract: tests/contract.
    """

    def get_run(self, run_id: str) -> RunRecord:
        """Raises RunNotFound."""
        ...

    def update_run(self, run_id: str, update: RunUpdate) -> None:
        """Raises RunNotFound, RunConflict if the status change is not allowed (also when a start
        was refused over the tenant's daily spend cap: the run is FAILED now), or RunDeferred if
        a start was put off because the tenant is at its cap on runs at once."""
        ...

    def defer_run(self, run_id: str, retry_after_seconds: int) -> RunStatus:
        """Sets a RUNNING run aside (the model provider keeps rate limiting): the control plane
        re-queues it after `retry_after_seconds` (1-900) and answers QUEUED, or FAILED
        (`rate_limited`) once the run was set aside for 15 minutes in total. Raises RunNotFound,
        or RunConflict if the run is not RUNNING."""
        ...
