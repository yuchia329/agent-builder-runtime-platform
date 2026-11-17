from dataclasses import dataclass
from typing import Any, Protocol

MAX_ITEM_BYTES = 350_000
"""Largest JSON payload one checkpoint or tool result may have (DynamoDB items stop at 400 KB)."""


@dataclass(frozen=True)
class Lease:
    """A worker's time-limited claim on a run. `last_step` is the highest checkpointed step."""

    run_id: str
    owner: str
    expires_at: float  # wall-clock epoch seconds
    last_step: int


@dataclass(frozen=True)
class Checkpoint:
    """What one step added to the run, as JSON-serializable data the store does not read."""

    step: int
    data: dict[str, Any]


@dataclass(frozen=True)
class ToolClaim:
    """Outcome of claiming a tool call's idempotency key.

    `fresh`: this call made the claim, so the caller runs the tool. Otherwise the key was claimed
    before: `result` is the stored result, or None if the earlier claimer never stored one (it
    may or may not have called the tool).
    """

    fresh: bool
    result: dict[str, Any] | None = None


class RunStateStore(Protocol):
    """Crash-safety state of runs: lease, checkpoints, tool-call idempotency keys.

    A lease is live while `owner` matches and `expires_at` is in the future. Every write except
    `take_lease` requires the caller's live lease (fencing): a worker that lost its lease raises
    LeaseLost and changes nothing, so it cannot overwrite the work of the worker that took over.
    Payloads must be JSON-serializable and at most MAX_ITEM_BYTES, else StateError. Adapters:
    DynamoDB, in-memory. Contract: tests/contract. Errors: LeaseLost, CheckpointConflict,
    StateError.
    """

    def take_lease(self, run_id: str, owner: str, seconds: float) -> Lease | None:
        """Takes or renews the lease for `seconds` from now. None if another worker holds a
        live lease. A run seen for the first time starts with `last_step` 0."""
        ...

    def renew_lease(self, run_id: str, owner: str, seconds: float) -> Lease:
        """Extends the caller's live lease to `seconds` from now (heartbeat)."""
        ...

    def release_lease(self, run_id: str, owner: str) -> None:
        """Ends the caller's lease so another worker can take the run at once. Does nothing if
        the caller does not hold it. Checkpoints are kept."""
        ...

    def save_checkpoint(self, run_id: str, owner: str, step: int, data: dict[str, Any]) -> None:
        """Saves step `step`, which must be the lease's `last_step` + 1 (else
        CheckpointConflict). A saved step is never changed."""
        ...

    def load_checkpoints(self, run_id: str) -> list[Checkpoint]:
        """All saved steps of the run, in step order; empty for a new run."""
        ...

    def claim_tool_call(self, run_id: str, owner: str, key: str) -> ToolClaim:
        """Claims `key` before the tool is called; see ToolClaim."""
        ...

    def complete_tool_call(self, run_id: str, owner: str, key: str, result: dict[str, Any]) -> None:
        """Stores the result of a claimed key. StateError if the key was never claimed or
        already has a result."""
        ...
