"""The worker's agent loop (ADR 0005, ADR 0009).

``handle_run(run_id, deps)`` loads a run, drives the model and tools until the run finishes,
pauses or exceeds its budget, and reports every status change to the control plane. It knows
nothing about how it was invoked (queue poll loop, Lambda) or which vendors sit behind the ports.

Every model reply passes through this loop before anything acts on it:

- a tool the spec does not list is never called; the model gets an error result instead;
- tool arguments are checked against the tool's schema before the call;
- a tool in ``require_approval`` stops the run in WAITING_APPROVAL without being called;
- a cut-off or refused reply ends the run as FAILED, never as a final answer;
- the budget is checked before every step;
- any unexpected error ends the run as FAILED with reason ``error``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from jsonschema import Draft202012Validator

from worker.core.budget import BudgetExceeded, BudgetTracker
from worker.core.errors import RunConflict, ToolError
from worker.core.types import (
    Failure,
    FailureReason,
    Message,
    PendingApproval,
    RunRecord,
    RunStatus,
    RunUpdate,
    StopReason,
    ToolCall,
    ToolResult,
    ToolSpec,
)
from worker.ports.clock import Clock
from worker.ports.control_plane import ControlPlane
from worker.ports.model import Model
from worker.ports.tools import ToolClient

log = logging.getLogger(__name__)

MAX_FAILURE_MESSAGE = 2000  # contract limit on failure.message


@dataclass(frozen=True)
class Deps:
    control_plane: ControlPlane
    model: Model
    tools: ToolClient
    clock: Clock


def handle_run(run_id: str, deps: Deps) -> RunStatus:
    """Executes one run and returns the status it ended in (or was already in)."""
    run = deps.control_plane.get_run(run_id)
    if run.status not in (RunStatus.QUEUED, RunStatus.RUNNING):
        # Duplicate delivery of a run that is finished or paused: nothing to do.
        log.info("run %s is %s; skipping", run_id, run.status)
        return run.status

    try:
        # QUEUED -> RUNNING, or RUNNING -> RUNNING when a crashed worker's run is redelivered.
        # Without checkpoints (M2) a resumed run starts over from the first step.
        deps.control_plane.update_run(run_id, RunUpdate(RunStatus.RUNNING, steps=0))
    except RunConflict:
        log.info("run %s was claimed or finished elsewhere; skipping", run_id)
        return deps.control_plane.get_run(run_id).status

    budget = BudgetTracker(run.spec.budget, deps.clock)
    try:
        final = _AgentLoop(run, deps, budget).run()
    except BudgetExceeded as e:
        final = _failed(budget, e.reason, e.message)
    except Exception as e:  # noqa: BLE001 - any crash must still end the run
        log.exception("run %s crashed", run_id)
        final = _failed(budget, FailureReason.ERROR, f"{type(e).__name__}: {e}")

    deps.control_plane.update_run(run_id, final)
    return final.status


def _failed(budget: BudgetTracker, reason: FailureReason, message: str) -> RunUpdate:
    return RunUpdate(
        RunStatus.FAILED,
        steps=budget.steps,
        failure=Failure(reason, message[:MAX_FAILURE_MESSAGE]),
    )


class _AgentLoop:
    def __init__(self, run: RunRecord, deps: Deps, budget: BudgetTracker) -> None:
        self._run = run
        self._spec = run.spec
        self._deps = deps
        self._budget = budget
        self._tools: list[ToolSpec] = deps.tools.describe(run.spec.tools)
        self._validators = {t.name: Draft202012Validator(t.input_schema) for t in self._tools}
        self._messages: list[Message] = [Message.user(_initial_prompt(run.input))]

    def run(self) -> RunUpdate:
        while True:
            self._budget.check()
            turn = self._deps.model.complete(self._spec.instructions, self._messages, self._tools)
            self._budget.record_step()
            self._messages.append(Message.assistant(turn))

            if turn.stop_reason is StopReason.REFUSAL:
                return self._fail("The model declined the request")
            if turn.stop_reason is StopReason.MAX_TOKENS:
                return self._fail("The model reply was cut off (max tokens)")
            if not turn.tool_calls:
                return RunUpdate(RunStatus.SUCCEEDED, steps=self._budget.steps, output=turn.text)

            for call in turn.tool_calls:
                rejection = self._rejection(call)
                if rejection is not None:
                    log.warning("run %s: rejected %s: %s", self._run.run_id, call.name, rejection)
                    result = ToolResult(call.id, rejection, is_error=True)
                elif call.name in self._spec.require_approval:
                    return self._pause_for_approval(call)
                else:
                    result = self._execute(call)
                self._messages.append(Message.tool(result))

    def _execute(self, call: ToolCall) -> ToolResult:
        """Runs an allowed, well-formed call. A tool failure becomes an error result."""
        self._budget.check()
        try:
            content = self._deps.tools.call(call.name, call.arguments)
            result = ToolResult(call.id, content)
        except ToolError as e:
            result = ToolResult(call.id, f"Tool failed: {e}", is_error=True)
        self._budget.record_step()
        return result

    def _rejection(self, call: ToolCall) -> str | None:
        if call.name not in self._spec.tools:
            allowed = ", ".join(self._spec.tools)
            return f"Tool '{call.name}' is not allowed. Allowed tools: {allowed}"
        errors = sorted(
            self._validators[call.name].iter_errors(call.arguments), key=lambda e: list(e.path)
        )
        if errors:
            details = "; ".join(f"{'.'.join(map(str, e.path)) or '$'}: {e.message}" for e in errors)
            return f"Invalid arguments for '{call.name}': {details}"
        return None

    def _pause_for_approval(self, call: ToolCall) -> RunUpdate:
        return RunUpdate(
            RunStatus.WAITING_APPROVAL,
            steps=self._budget.steps,
            pending_approval=PendingApproval(
                step=self._budget.steps + 1, tool=call.name, arguments=call.arguments
            ),
        )

    def _fail(self, message: str) -> RunUpdate:
        return _failed(self._budget, FailureReason.ERROR, message)


def _initial_prompt(run_input: dict[str, Any]) -> str:
    return "Run input (JSON):\n" + json.dumps(run_input, indent=2, sort_keys=True)
