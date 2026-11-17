"""The worker's agent loop (ADR 0005, ADR 0009).

``handle_run(run_id, deps)`` loads a run, drives the model and tools until the run finishes,
pauses or exceeds its budget, and reports every status change to the control plane. It knows
nothing about how it was invoked (queue poll loop, Lambda) or which vendors sit behind the ports.

Every model reply passes through this loop before anything acts on it:

- a tool the spec does not list is never called; the model gets an error result instead;
- tool arguments are checked against the tool's schema before the call;
- a tool in ``require_approval`` stops the run in WAITING_APPROVAL without being called;
- a cut-off or refused reply ends the run as FAILED, never as a final answer;
- the budget (steps, dollars, seconds) is checked before every step;
- a worker that lost the run's lease (``cancelled()`` turns true) stops before its next step
  with ``LeaseLost`` and reports nothing: another worker owns the run now;
- a model with no known price or context size fails the run before its first step;
- the conversation is trimmed before a call once it nears the model's context window;
- any unexpected error ends the run as FAILED with reason ``error``.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from jsonschema import Draft202012Validator

from worker.core.budget import BudgetExceeded, BudgetTracker
from worker.core.errors import LeaseLost, RunConflict, ToolError
from worker.core.models import MODELS, ModelInfo, UnknownModel, model_info
from worker.core.trim import ContextTooLarge, fit_context, message_chars
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
TRIM_AT = 0.7  # share of the context window the conversation may fill; the rest is for the reply
DEFAULT_TOKENS_PER_CHAR = 1 / 3  # until the first reply reports real usage; errs high for English


@dataclass(frozen=True)
class Deps:
    control_plane: ControlPlane
    model: Model
    tools: ToolClient
    clock: Clock
    models: Mapping[str, ModelInfo] = field(default_factory=lambda: MODELS)


def handle_run(run_id: str, deps: Deps, cancelled: Callable[[], bool] | None = None) -> RunStatus:
    """Executes one run and returns the status it ended in (or was already in).

    ``cancelled`` is checked before every step; once it returns True the run stops with
    LeaseLost, leaving its status to the worker that holds the lease now."""
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
        info = model_info(run.spec.model, dict(deps.models))
        final = _AgentLoop(run, deps, budget, info, cancelled or _never).run()
    except LeaseLost:
        log.warning("run %s: lease lost; stopping, another worker owns the run", run_id)
        raise
    except (UnknownModel, ContextTooLarge) as e:
        final = _failed(budget, FailureReason.ERROR, str(e))
    except BudgetExceeded as e:
        final = _failed(budget, e.reason, e.message)
    except Exception as e:  # noqa: BLE001 - any crash must still end the run
        log.exception("run %s crashed", run_id)
        final = _failed(budget, FailureReason.ERROR, f"{type(e).__name__}: {e}")

    deps.control_plane.update_run(run_id, final)
    return final.status


def _never() -> bool:
    return False


def _failed(budget: BudgetTracker, reason: FailureReason, message: str) -> RunUpdate:
    return RunUpdate(
        RunStatus.FAILED,
        steps=budget.steps,
        cost_usd=_cost(budget),
        failure=Failure(reason, message[:MAX_FAILURE_MESSAGE]),
    )


def _cost(budget: BudgetTracker) -> float:
    return round(budget.cost_usd, 6)


class _AgentLoop:
    def __init__(
        self,
        run: RunRecord,
        deps: Deps,
        budget: BudgetTracker,
        info: ModelInfo,
        cancelled: Callable[[], bool],
    ) -> None:
        self._run = run
        self._cancelled = cancelled
        self._spec = run.spec
        self._deps = deps
        self._budget = budget
        self._price = info.price
        self._context_limit = int(info.context_tokens * TRIM_AT)
        self._tools: list[ToolSpec] = deps.tools.describe(run.spec.tools)
        self._validators = {t.name: Draft202012Validator(t.input_schema) for t in self._tools}
        self._messages: list[Message] = [Message.user(_initial_prompt(run.input))]
        # System prompt and tool schemas are sent with every call.
        self._fixed_chars = len(run.spec.instructions) + sum(
            len(t.name) + len(t.description) + len(json.dumps(t.input_schema)) for t in self._tools
        )
        self._tokens_per_char = DEFAULT_TOKENS_PER_CHAR

    def run(self) -> RunUpdate:
        while True:
            self._before_step()
            self._fit_context()
            turn = self._deps.model.complete(
                self._spec.model, self._spec.instructions, self._messages, self._tools
            )
            self._learn_token_ratio(turn.usage.input_tokens)
            self._budget.record_step(self._price.cost_usd(turn.usage))
            self._messages.append(Message.assistant(turn))

            if turn.stop_reason is StopReason.REFUSAL:
                return self._fail("The model declined the request")
            if turn.stop_reason is StopReason.MAX_TOKENS:
                return self._fail("The model reply was cut off (max tokens)")
            if not turn.tool_calls:
                return RunUpdate(
                    RunStatus.SUCCEEDED,
                    steps=self._budget.steps,
                    cost_usd=_cost(self._budget),
                    output=turn.text,
                )

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

    def _estimate(self, messages: Sequence[Message]) -> int:
        chars = self._fixed_chars + sum(message_chars(m) for m in messages)
        return int(chars * self._tokens_per_char)

    def _fit_context(self) -> None:
        fitted = fit_context(self._messages, self._context_limit, self._estimate)
        if fitted != self._messages:
            log.info(
                "run %s: trimmed conversation to fit the context window (%d -> %d messages)",
                self._run.run_id,
                len(self._messages),
                len(fitted),
            )
        self._messages = fitted

    def _learn_token_ratio(self, input_tokens: int) -> None:
        """Calibrates the estimate with the tokens the provider counted for what we sent."""
        chars = self._fixed_chars + sum(message_chars(m) for m in self._messages)
        if input_tokens > 0 and chars > 0:
            self._tokens_per_char = input_tokens / chars

    def _before_step(self) -> None:
        if self._cancelled():
            raise LeaseLost(f"Run {self._run.run_id}: lease lost")
        self._budget.check()

    def _execute(self, call: ToolCall) -> ToolResult:
        """Runs an allowed, well-formed call. A tool failure becomes an error result."""
        self._before_step()
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
            cost_usd=_cost(self._budget),
            pending_approval=PendingApproval(
                step=self._budget.steps + 1, tool=call.name, arguments=call.arguments
            ),
        )

    def _fail(self, message: str) -> RunUpdate:
        return _failed(self._budget, FailureReason.ERROR, message)


def _initial_prompt(run_input: dict[str, Any]) -> str:
    return "Run input (JSON):\n" + json.dumps(run_input, indent=2, sort_keys=True)
