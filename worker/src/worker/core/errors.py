"""Errors that ports raise and the core handles. Adapters translate vendor errors into these."""

from worker.core.types import RunStatus


class RunNotFound(Exception):
    """The control plane has no run with this id."""


class RunConflict(Exception):
    """The control plane refused a status change (HTTP 409)."""

    def __init__(self, run_id: str, requested: RunStatus, detail: str = "") -> None:
        super().__init__(f"Run {run_id} cannot move to {requested}: {detail}".rstrip(": "))
        self.requested = requested


class RunDeferred(Exception):
    """The control plane put off the run's start (HTTP 429 Deferred): the tenant is at its cap
    on runs executing at once. It has already re-queued the run after `retry_after_seconds`; the
    run stays QUEUED and the worker drops its message (ADR 0026)."""

    def __init__(self, run_id: str, retry_after_seconds: int) -> None:
        super().__init__(f"Run {run_id} deferred for {retry_after_seconds}s: tenant at its cap")
        self.retry_after_seconds = retry_after_seconds


class ToolError(Exception):
    """A tool call failed. The message is shown to the model, so it must not contain secrets."""


class ModelError(Exception):
    """The model call failed after the adapter's own retries."""


class ModelRateLimited(ModelError):
    """The model provider still answers "too many requests" after the adapter's retries. The
    loop sets the run aside instead of failing it (ADR 0027). `retry_after` is the wait in
    seconds the provider asked for, if it said."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class ControlPlaneError(Exception):
    """The control plane could not be reached, kept failing after retries, or answered with an
    unexpected status. The run's queue message is kept, so the run is retried later."""


class QueueError(Exception):
    """A run queue call failed (receive, delete, or extend a message)."""


class LeaseLost(Exception):
    """This worker no longer holds the run's lease (expired, released, or taken by another
    worker). The run state was not changed; the worker must stop working on the run."""


class CheckpointConflict(Exception):
    """A checkpoint was not the next step (step n is saved only right after step n-1)."""


class StateError(Exception):
    """The run-state store failed (unreachable, throttled after retries, item too large)."""
