"""One JSON line per tool call on the `gateway.access` logger: who ran what, as whom, and how it
ended. Never argument values or the token: arguments are logged as a SHA-256 of canonical JSON
(the worker's canonical form: sorted keys, no spaces)."""

import hashlib
import json
import logging
from typing import Any

from gateway.auth.run_token import RunClaims

log = logging.getLogger("gateway.access")


def arguments_hash(arguments: dict[str, Any]) -> str:
    canonical = json.dumps(arguments, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def acting_as(claims: RunClaims) -> str:
    return claims.owner if claims.acts_as == "owner" else "team-bot"


def record(
    claims: RunClaims,
    tool: str,
    step: int | None,
    idempotency_key: str | None,
    arguments: dict[str, Any],
    outcome: str,
    duration_ms: float,
    reason: str | None = None,
) -> None:
    entry = {
        "event": "tool_call",
        "run_id": claims.run_id,
        "tenant": claims.tenant,
        "agent": f"{claims.agent}@{claims.agent_version}",
        "started_by": claims.started_by,
        "acts_as": claims.acts_as,
        "identity": acting_as(claims),
        "tool": tool,
        "step": step,
        "idempotency_key": idempotency_key,
        "arguments_sha256": arguments_hash(arguments),
        "outcome": outcome,
        "duration_ms": round(duration_ms, 1),
    }
    if reason:
        entry["reason"] = reason
    log.info(json.dumps(entry, sort_keys=True))
