"""Runs one task: the worker's `handle_run` with in-memory adapters and the given model, tools
through the real gateway, simulated services underneath (ADR 0016).

`Evaluation` holds what lives for a whole suite (simulator, internal run endpoint, gateway); each
`run` resets the simulator to the task's world, stores the run like the control plane would,
signs a run token for every attempt, approves every approval request (the checks decide whether
it should have been asked), and evaluates the task's checks.
"""

from __future__ import annotations

import itertools
import logging
from collections.abc import Sequence
from contextlib import ExitStack
from dataclasses import dataclass
from typing import Any

from worker.adapters.clock_system import SystemClock
from worker.adapters.control_plane_memory import InMemoryControlPlane
from worker.adapters.run_state_memory import InMemoryRunStateStore
from worker.adapters.tools_mcp import GatewayTools
from worker.core.errors import ToolError
from worker.core.handle_run import Attempt, Deps, handle_run
from worker.core.types import RunStatus, ToolContext, ToolSpec
from worker.ports.model import Model

from evals.checks import CheckResult, Outcome, ToolCallRecord, evaluate
from evals.services import TENANT, GatewayProcess, InternalRunServer, RunTokens
from evals.simulators.github import GitHubSimulator
from evals.task import Task

log = logging.getLogger(__name__)

MAX_APPROVALS = 5  # more approval requests than this in one run fail the task
OWNER = "evals/1"  # lease owner: checkpoints let a run continue after its approval
LEASE_SECONDS = 3600.0
_numbers = itertools.count(1)


class RecordingTools:
    """Wraps a ToolClient and keeps every call core makes, with its tool error if any."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.calls: list[ToolCallRecord] = []

    def describe(self, names: Sequence[str], context: ToolContext) -> list[ToolSpec]:
        return self._inner.describe(names, context)

    def call(self, name: str, arguments: dict[str, Any], context: ToolContext) -> Any:
        try:
            result = self._inner.call(name, arguments, context)
        except ToolError as e:
            self.calls.append(ToolCallRecord(name, dict(arguments), str(e) or "tool error"))
            raise
        self.calls.append(ToolCallRecord(name, dict(arguments)))
        return result


@dataclass(frozen=True)
class TaskResult:
    task: Task
    run_id: str
    outcome: Outcome
    checks: tuple[CheckResult, ...]

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)


class Evaluation:
    """Context manager: simulators, internal run endpoint and gateway, started once."""

    def __init__(self) -> None:
        self.control_plane = InMemoryControlPlane()
        self.tokens = RunTokens()
        self._stack = ExitStack()
        self.github: GitHubSimulator | None = None
        self.gateway: GatewayProcess | None = None

    def __enter__(self) -> Evaluation:
        try:
            self.github = self._stack.enter_context(GitHubSimulator())
            internal = self._stack.enter_context(InternalRunServer(self.control_plane))
            self.gateway = self._stack.enter_context(
                GatewayProcess(self.tokens, self.github.url, internal.url)
            )
        except BaseException:
            self._stack.close()
            raise
        return self

    def __exit__(self, *exc: object) -> None:
        self._stack.close()

    def run(self, task: Task, model: Model) -> TaskResult:
        assert self.github is not None and self.gateway is not None, "use as a context manager"
        self.github.load(task.world.get("github"))
        run_id = f"run_eval_{next(_numbers):04d}_{task.name}"[:100]
        run = {
            "run_id": run_id,
            "tenant_id": TENANT,
            "agent_name": task.spec["name"],
            "agent_version": 1,
            "status": "QUEUED",
            "steps": 0,
            "cost_usd": 0,
            "created_at": "2026-11-18T00:00:00Z",
            "updated_at": "2026-11-18T00:00:00Z",
            "spec": task.spec,
            "input": task.input,
        }
        self.control_plane.add_run(run)
        tools = RecordingTools(GatewayTools(self.gateway.url))
        clock = SystemClock()
        state = InMemoryRunStateStore(clock)
        deps = Deps(self.control_plane, model, tools, clock, state=state)
        approvals: list[dict[str, Any]] = []
        while True:
            self._set_token(run_id)
            # Like the poll loop: lease, then one attempt; checkpoints carry the conversation
            # across the approval pause instead of starting over.
            if state.take_lease(run_id, OWNER, LEASE_SECONDS) is None:
                raise RuntimeError(f"run {run_id}: lease not available")
            try:
                status = handle_run(run_id, deps, Attempt(OWNER, lambda: False))
            finally:
                state.release_lease(run_id, OWNER)
            if status is not RunStatus.WAITING_APPROVAL:
                break
            pending = self.control_plane.run_json(run_id)["pending_approval"]
            approvals.append(pending)
            if len(approvals) > MAX_APPROVALS:
                log.warning("run %s: more than %d approval requests", run_id, MAX_APPROVALS)
                break
            self.control_plane.decide(run_id, "approved", decided_by="eval:auto")
        stored = self.control_plane.run_json(run_id)
        outcome = Outcome(
            status=stored["status"],
            failure=stored.get("failure"),
            output=stored.get("output"),
            steps=stored.get("steps", 0),
            cost_usd=stored.get("cost_usd", 0.0),
            calls=tuple(tools.calls),
            approvals=tuple(approvals),
            world={"github": self.github.snapshot()},
        )
        checks = tuple(evaluate(c, outcome) for c in task.checks)
        return TaskResult(task, run_id, outcome, checks)

    def _set_token(self, run_id: str) -> None:
        """A new token for each attempt, as GET /internal/runs/{id} gives one."""
        run = self.control_plane.run_json(run_id)
        run["run_token"] = self.tokens.sign(run)
        self.control_plane.add_run(run)
