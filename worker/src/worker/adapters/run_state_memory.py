"""RunStateStore in memory: tests and single-process local runs. Same rules as the DynamoDB
adapter, enforced under one lock; lost on restart, so it gives no real crash safety."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from typing import Any

from worker.adapters.run_state_payload import encode
from worker.core.errors import CheckpointConflict, LeaseLost, StateError
from worker.ports.clock import Clock
from worker.ports.run_state import Checkpoint, Lease, ToolClaim


@dataclass
class _Run:
    owner: str = ""
    expires_at: float = 0.0
    last_step: int = 0
    steps: dict[int, str] = field(default_factory=dict)  # step -> JSON
    tools: dict[str, str | None] = field(default_factory=dict)  # key -> result JSON or None


class InMemoryRunStateStore:
    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._runs: dict[str, _Run] = {}
        self._lock = threading.Lock()

    def take_lease(self, run_id: str, owner: str, seconds: float) -> Lease | None:
        with self._lock:
            run = self._runs.setdefault(run_id, _Run())
            now = self._clock.time()
            if run.owner != owner and run.expires_at > now:
                return None
            run.owner, run.expires_at = owner, now + seconds
            return Lease(run_id, owner, run.expires_at, run.last_step)

    def renew_lease(self, run_id: str, owner: str, seconds: float) -> Lease:
        with self._lock:
            run = self._live(run_id, owner)
            run.expires_at = self._clock.time() + seconds
            return Lease(run_id, owner, run.expires_at, run.last_step)

    def release_lease(self, run_id: str, owner: str) -> None:
        with self._lock:
            run = self._runs.get(run_id)
            if run is not None and run.owner == owner:
                run.expires_at = 0.0

    def save_checkpoint(self, run_id: str, owner: str, step: int, data: dict[str, Any]) -> None:
        payload = encode(data, f"Checkpoint {step}")
        with self._lock:
            run = self._live(run_id, owner)
            if step != run.last_step + 1:
                raise CheckpointConflict(
                    f"Run {run_id}: step {step} is not next (last saved {run.last_step})"
                )
            run.steps[step] = payload
            run.last_step = step

    def load_checkpoints(self, run_id: str) -> list[Checkpoint]:
        with self._lock:
            run = self._runs.get(run_id)
            steps = sorted(run.steps.items()) if run else []
        return [Checkpoint(step, json.loads(payload)) for step, payload in steps]

    def claim_tool_call(self, run_id: str, owner: str, key: str) -> ToolClaim:
        with self._lock:
            run = self._live(run_id, owner)
            if key not in run.tools:
                run.tools[key] = None
                return ToolClaim(fresh=True)
            stored = run.tools[key]
        return ToolClaim(fresh=False, result=None if stored is None else json.loads(stored))

    def complete_tool_call(self, run_id: str, owner: str, key: str, result: dict[str, Any]) -> None:
        payload = encode(result, f"Tool result {key}")
        with self._lock:
            run = self._live(run_id, owner)
            if key not in run.tools or run.tools[key] is not None:
                raise StateError(f"Run {run_id}: tool key {key} is not claimed without a result")
            run.tools[key] = payload

    def _live(self, run_id: str, owner: str) -> _Run:
        run = self._runs.get(run_id)
        if run is None or run.owner != owner or run.expires_at <= self._clock.time():
            raise LeaseLost(f"Run {run_id}: {owner} does not hold a live lease")
        return run
