"""Second approval check (ADR 0003): a tool in the token's require_approval runs only if the run's
latest approval, read from the control plane, approves this exact call.

The worker checks first (worker/.../core/handle_run.py); this check makes the gateway not depend
on it. A call matches when the decision is `approved` and its step, tool and canonical arguments
equal the call's; the run must also be this token's run and tenant, and RUNNING (a finished run
cannot replay a call while its token lasts). Anything else, including a control plane that cannot
answer, refuses the call. `check` returns the reason for the access log, or None to allow.
"""

import json
import logging
from typing import Any

from gateway.auth.run_token import RunClaims
from gateway.ports.control_plane import ControlPlane, ControlPlaneUnavailable, RunNotFound

log = logging.getLogger("gateway")


def canonical(value: Any) -> str:
    """JSON text equal only for equal JSON values; the same function as the worker's."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


class ApprovalCheck:
    def __init__(self, control_plane: ControlPlane | None) -> None:
        self._control_plane = control_plane

    @property
    def configured(self) -> bool:
        return self._control_plane is not None

    def check(
        self, claims: RunClaims, tool: str, step: int, arguments: dict[str, Any]
    ) -> str | None:
        if self._control_plane is None:
            return "approval check not configured"
        try:
            run = self._control_plane.get_run(claims.run_id)
        except RunNotFound:
            return "run not found"
        except ControlPlaneUnavailable as e:
            log.warning("Approval check for run %s: %s", claims.run_id, e)
            return "control plane unavailable"
        if run.get("run_id") != claims.run_id or run.get("tenant_id") != claims.tenant:
            return "run does not match the token"
        if run.get("status") != "RUNNING":
            return "run not running"
        approval = run.get("approval")
        if not isinstance(approval, dict):
            return "no approval"
        if approval.get("decision") != "approved":
            return "approval not approved"
        approval_step = approval.get("step")
        if isinstance(approval_step, bool) or approval_step != step:
            return "approval for another step"
        if approval.get("tool") != tool:
            return "approval for another tool"
        if canonical(approval.get("arguments")) != canonical(arguments):
            return "approval for other arguments"
        return None
