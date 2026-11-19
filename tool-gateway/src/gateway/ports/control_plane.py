"""Port: reading a run from the control plane (GET /internal/runs/{id},
contracts/internal-api.openapi.yaml), for the second approval check (ADR 0003).

Every adapter passes tests/contract/test_control_plane.py.
"""

from typing import Any, Protocol


class RunNotFound(Exception):
    """The control plane has no such run (404)."""


class ControlPlaneUnavailable(Exception):
    """The control plane could not answer: unreachable, an error status, or a body that is not a
    run. The message names the status, never a token."""


class ControlPlane(Protocol):
    def get_run(self, run_id: str) -> dict[str, Any]:
        """The run as GET /internal/runs/{run_id} returns it (an InternalRun object)."""
        ...
