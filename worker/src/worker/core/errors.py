"""Errors that ports raise and the core handles. Adapters translate vendor errors into these."""

from worker.core.types import RunStatus


class RunNotFound(Exception):
    """The control plane has no run with this id."""


class RunConflict(Exception):
    """The control plane refused a status change (HTTP 409)."""

    def __init__(self, run_id: str, requested: RunStatus, detail: str = "") -> None:
        super().__init__(f"Run {run_id} cannot move to {requested}: {detail}".rstrip(": "))
        self.requested = requested


class ToolError(Exception):
    """A tool call failed. The message is shown to the model, so it must not contain secrets."""


class ModelError(Exception):
    """The model call failed after the adapter's own retries."""


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
