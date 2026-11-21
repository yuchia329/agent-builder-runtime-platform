"""In-memory ControlPlane for tests. Enforces the same status rules as the real control plane,
and a simple form of its tenant limits (ADR 0026, ADR 0027): a cap on runs RUNNING at once per
tenant (`max_running`), tenants over their daily spend cap (`over_budget`), and setting runs
aside with the 15-minute total."""

from __future__ import annotations

import copy
import threading
from typing import Any

from worker.core.errors import RunConflict, RunDeferred, RunNotFound
from worker.core.types import RunRecord, RunStatus, RunUpdate

# Mirrors the status-change table in contracts/internal-api.openapi.yaml.
ALLOWED_FROM: dict[RunStatus, set[RunStatus]] = {
    RunStatus.RUNNING: {RunStatus.QUEUED, RunStatus.RUNNING},
    RunStatus.WAITING_APPROVAL: {RunStatus.RUNNING},
    RunStatus.SUCCEEDED: {RunStatus.RUNNING},
    RunStatus.FAILED: {RunStatus.RUNNING},
    RunStatus.QUEUED: set(),
}


MAX_SET_ASIDE_SECONDS = 900  # after this much time set aside in total, a run fails rate_limited
CAP_RETRY_SECONDS = 30  # Retry-After of a start put off at the cap


class InMemoryControlPlane:
    def __init__(self, max_running: int | None = None) -> None:
        self._runs: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self.max_running = max_running  # per tenant; None: no cap
        self.over_budget: set[str] = set()  # tenants whose queued runs fail tenant_budget
        self._set_aside: dict[str, int] = {}  # run_id -> seconds set aside in total
        self.updates: list[tuple[str, dict[str, Any]]] = []  # (run_id, PATCH body) in order
        self.deferrals: list[tuple[str, dict[str, Any]]] = []  # (run_id, defer body) in order

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
            if current is RunStatus.QUEUED and requested is RunStatus.RUNNING:
                self._check_start(run)
            run.update(copy.deepcopy(body))
            self.updates.append((run_id, copy.deepcopy(body)))

    def _check_start(self, run: dict[str, Any]) -> None:
        tenant = run["tenant_id"]
        if tenant in self.over_budget:
            run["status"] = RunStatus.FAILED.value
            run["failure"] = {"reason": "tenant_budget", "message": "over the daily spend cap"}
            raise RunConflict(run["run_id"], RunStatus.RUNNING, "tenant over its daily spend cap")
        running = sum(
            1
            for r in self._runs.values()
            if r["tenant_id"] == tenant and r["status"] == RunStatus.RUNNING
        )
        if self.max_running is not None and running >= self.max_running:
            raise RunDeferred(run["run_id"], CAP_RETRY_SECONDS)

    def defer_run(self, run_id: str, retry_after_seconds: int) -> RunStatus:
        return self.defer(
            run_id, {"reason": "rate_limited", "retry_after_seconds": retry_after_seconds}
        )

    def defer(self, run_id: str, body: dict[str, Any]) -> RunStatus:
        """Applies a defer body (already valid against RunDefer). Shared with the fake HTTP
        server."""
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                raise RunNotFound(run_id)
            if run["status"] != RunStatus.RUNNING:
                raise RunConflict(run_id, RunStatus.QUEUED, f"run is {run['status']}")
            self.deferrals.append((run_id, copy.deepcopy(body)))
            total = self._set_aside.get(run_id, 0)
            if total >= MAX_SET_ASIDE_SECONDS:
                run["status"] = RunStatus.FAILED.value
                run["failure"] = {
                    "reason": "rate_limited",
                    "message": f"set aside for {total // 60} minutes in total",
                }
            else:
                self._set_aside[run_id] = total + body["retry_after_seconds"]
                run["status"] = RunStatus.QUEUED.value
            return RunStatus(run["status"])

    def set_aside_seconds(self, run_id: str) -> int:
        with self._lock:
            return self._set_aside.get(run_id, 0)

    def decide(self, run_id: str, decision: str, decided_by: str = "api:tenant-demo") -> None:
        """Test helper doing what the control plane does on a decision (POST
        /runs/{id}/approval): copy the pending call into `approval`, clear `pending_approval`,
        and queue the run (approved) or fail it with approval_rejected (rejected)."""
        with self._lock:
            run = self._runs[run_id]
            if run["status"] != RunStatus.WAITING_APPROVAL:
                raise RunConflict(run_id, RunStatus.QUEUED, f"run is {run['status']}")
            pending = run.pop("pending_approval")
            run["approval"] = pending | {
                "decision": decision,
                "decided_by": decided_by,
                "decided_at": "2026-11-17T12:00:00Z",
            }
            if decision == "approved":
                run["status"] = RunStatus.QUEUED.value
            else:
                run["status"] = RunStatus.FAILED.value
                run["failure"] = {
                    "reason": "approval_rejected",
                    "message": f"rejected by {decided_by}",
                }
