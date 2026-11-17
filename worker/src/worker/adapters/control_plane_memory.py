"""In-memory ControlPlane for tests. Enforces the same status rules as the real control plane."""

from __future__ import annotations

import copy
import threading
from typing import Any

from worker.core.errors import RunConflict, RunNotFound
from worker.core.types import RunRecord, RunStatus, RunUpdate

# Mirrors the status-change table in contracts/internal-api.openapi.yaml.
ALLOWED_FROM: dict[RunStatus, set[RunStatus]] = {
    RunStatus.RUNNING: {RunStatus.QUEUED, RunStatus.RUNNING},
    RunStatus.WAITING_APPROVAL: {RunStatus.RUNNING},
    RunStatus.SUCCEEDED: {RunStatus.RUNNING},
    RunStatus.FAILED: {RunStatus.RUNNING},
    RunStatus.QUEUED: set(),
}


class InMemoryControlPlane:
    def __init__(self) -> None:
        self._runs: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self.updates: list[tuple[str, dict[str, Any]]] = []  # (run_id, PATCH body) in order

    def add_run(self, run: dict[str, Any]) -> None:
        """Stores a run in the GET /internal/runs/{id} JSON shape."""
        with self._lock:
            self._runs[run["run_id"]] = copy.deepcopy(run)

    def run_json(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._runs[run_id])

    def get_run(self, run_id: str) -> RunRecord:
        with self._lock:
            if run_id not in self._runs:
                raise RunNotFound(run_id)
            return RunRecord.from_dict(copy.deepcopy(self._runs[run_id]))

    def update_run(self, run_id: str, update: RunUpdate) -> None:
        self.apply(run_id, update.to_dict())

    def apply(self, run_id: str, body: dict[str, Any]) -> None:
        """Applies a PATCH body (already valid against RunUpdate). Shared with the fake HTTP
        server in tests, so both adapters see the same status rules."""
        requested = RunStatus(body["status"])
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                raise RunNotFound(run_id)
            current = RunStatus(run["status"])
            if current not in ALLOWED_FROM[requested]:
                raise RunConflict(run_id, requested, f"run is {current}")
            run.update(copy.deepcopy(body))
            self.updates.append((run_id, copy.deepcopy(body)))
