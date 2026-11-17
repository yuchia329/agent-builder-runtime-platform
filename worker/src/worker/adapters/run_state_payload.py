"""Payload rule shared by the RunStateStore adapters."""

import json
from typing import Any

from worker.core.errors import StateError
from worker.ports.run_state import MAX_ITEM_BYTES


def encode(data: dict[str, Any], what: str) -> str:
    """Compact JSON for a payload; StateError if not serializable or over MAX_ITEM_BYTES."""
    try:
        payload = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError) as e:
        raise StateError(f"{what} is not JSON-serializable: {e}") from e
    size = len(payload.encode())
    if size > MAX_ITEM_BYTES:
        raise StateError(f"{what} is {size} bytes; the limit is {MAX_ITEM_BYTES}")
    return payload
