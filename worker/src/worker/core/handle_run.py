"""The worker's agent loop (ADR 0005, ADR 0009, ADR 0011).

``handle_run(run_id, deps, attempt)`` loads a run, drives the model and tools until the run
finishes, pauses or exceeds its budget, and reports every status change to the control plane. It
knows nothing about how it was invoked (queue poll loop, Lambda) or which vendors sit behind the
ports.

Every model reply passes through this loop before anything acts on it:

- a tool the spec does not list is never called; the model gets an error result instead;
- tool arguments are checked against the tool's schema before the call;
- a tool in ``require_approval`` stops the run in WAITING_APPROVAL without being called, unless
  the run's approval decision is ``approved`` for this exact call (same step, tool and arguments
  as canonical JSON); then it is called like any other tool. Anything else pauses again;
- a cut-off or refused reply ends the run as FAILED, never as a final answer;
- the budget (steps, dollars, working seconds) is checked before every step;
- a worker that lost the run's lease (``attempt.cancelled()`` turns true, or a checkpoint write
  raises LeaseLost) stops with ``LeaseLost`` and reports nothing: another worker owns the run;
- every tool call claims an idempotency key first; a resumed run reuses a stored result instead
  of calling again, and a call whose outcome a crash left unknown is repeated only if the tool
  is read-only or idempotent, else the run fails (never a duplicate side effect);
- a model with no known price or context size fails the run before its first step;
- a copy of the conversation is trimmed before a call once it nears the model's context window;
- any unexpected error ends the run as FAILED with reason ``error``.

Checkpoints (with an ``attempt`` and ``deps.state``): after every step the messages it added, its
cost, the working seconds so far and (model steps) the stop reason are saved as step n. A run
picked up again rebuilds its history from them and continues: tool calls of the last model turn
without a saved result are processed again, then the next model turn. The run's input message is
not saved; it is rebuilt from the run. If the run-state store fails (StateError), the attempt
stops without reporting, and the run resumes later from its last checkpoint.

Tracing (``deps.tracer``, ADR 0022): a span per model call (``model.call``), tool call
(``tool.call``) and checkpoint write (``checkpoint``), children of the caller's span (the poll
loop's ``run.attempt``). Attributes are ids, counts and names; never input, output or arguments.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from jsonschema import Draft202012Validator

from worker.core.budget import BudgetExceeded, BudgetTracker
from worker.core.errors import LeaseLost, RunConflict, StateError, ToolError
from worker.core.models import MODELS, ModelInfo, UnknownModel, model_info
from worker.core.tracing import NoopTracer
from worker.core.trim import ContextTooLarge, fit_context, message_chars
from worker.core.types import (
    Decision,
    Failure,
    FailureReason,
    Message,
    PendingApproval,
    RunRecord,
    RunStatus,
    RunUpdate,
    StopReason,
    ToolCall,
    ToolContext,
    ToolResult,
    ToolSpec,
)
from worker.ports.clock import Clock
from worker.ports.control_plane import ControlPlane
from worker.ports.model import Model
from worker.ports.run_state import Checkpoint, RunStateStore
from worker.ports.tools import ToolClient
from worker.ports.tracing import Tracer

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
    state: RunStateStore | None = None  # checkpoints; None: nothing is saved
    tracer: Tracer = field(default_factory=NoopTracer)


@dataclass(frozen=True)
class Attempt:
    """One worker's turn at a run, under its lease: `owner` signs checkpoint writes,
    `cancelled()` turns true once the lease is lost."""

    owner: str
    cancelled: Callable[[], bool]


@dataclass(frozen=True)
class _Resume:
    """What the checkpoints of a run add up to."""

    messages: list[Message]
    steps: int
    cost_usd: float
    elapsed: float
    last_turn: tuple[Message, StopReason] | None  # the latest model turn, if any


def handle_run(run_id: str, deps: Deps, attempt: Attempt | None = None) -> RunStatus:
    """Executes one run and returns the status it ended in (or was already in)."""
    run = deps.control_plane.get_run(run_id)
    deps.tracer.annotate(
        {
            "run.id": run.run_id,
            "tenant.id": run.tenant_id,
            "agent.name": run.agent_name,
            "agent.version": run.agent_version,
        }
    )
    if run.status not in (RunStatus.QUEUED, RunStatus.RUNNING):
        # Duplicate delivery of a run that is finished or paused: nothing to do.
        log.info("run %s is %s; skipping", run_id, run.status)
        return run.status

    saving = attempt is not None and deps.state is not None
    resume = _resume(deps.state.load_checkpoints(run_id)) if saving else _resume([])
    if resume.steps:
        log.info("run %s: resuming after step %d", run_id, resume.steps)
    try:
        # QUEUED -> RUNNING, or RUNNING -> RUNNING when a run is picked up again.
        deps.control_plane.update_run(run_id, RunUpdate(RunStatus.RUNNING, steps=resume.steps))
    except RunConflict:
        log.info("run %s was claimed or finished elsewhere; skipping", run_id)
        return deps.control_plane.get_run(run_id).status

    budget = BudgetTracker(
        run.spec.budget, deps.clock, resume.steps, resume.cost_usd, resume.elapsed
    )
    try:
        info = model_info(run.spec.model, dict(deps.models))
        final = _AgentLoop(run, deps, budget, info, attempt, resume).run()
    except LeaseLost:
        log.warning("run %s: lease lost; stopping, another worker owns the run", run_id)
        raise
    except StateError:
        log.error("run %s: run-state store failed; stopping, resumes later", run_id)
        raise
    except (UnknownModel, ContextTooLarge, ToolOutcomeUnknown) as e:
        final = _failed(budget, FailureReason.ERROR, str(e))
    except BudgetExceeded as e:
        final = _failed(budget, e.reason, e.message)
    except Exception as e:  # noqa: BLE001 - any crash must still end the run
        log.exception("run %s crashed", run_id)
        final = _failed(budget, FailureReason.ERROR, f"{type(e).__name__}: {e}")

    deps.control_plane.update_run(run_id, final)
    return final.status


class ToolOutcomeUnknown(Exception):
    """A claimed call has no stored result (the worker died during or right after it) and the
    tool is not safe to repeat."""


def tool_call_key(run_id: str, step: int, call: ToolCall) -> str:
    """Idempotency key of a tool call: the same call at the same step gets the same key."""
    canonical = json.dumps(
        [run_id, step, call.name, call.arguments], sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _resume(checkpoints: Sequence[Checkpoint]) -> _Resume:
    messages: list[Message] = []
    cost, elapsed = 0.0, 0.0
    last_turn: tuple[Message, StopReason] | None = None
    for c in checkpoints:
        added = [Message.from_json(m) for m in c.data["messages"]]
        messages.extend(added)
        cost += c.data["cost_usd"]
        elapsed = c.data["elapsed_s"]
        if "stop_reason" in c.data:
            turn = next(m for m in reversed(added) if m.role == "assistant")
            last_turn = (turn, StopReason(c.data["stop_reason"]))
    steps = checkpoints[-1].step if checkpoints else 0
    return _Resume(messages, steps, cost, elapsed, last_turn)


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
        attempt: Attempt | None,
        resume: _Resume,
    ) -> None:
        self._run = run
        self._attempt = attempt
        self._spec = run.spec
        self._deps = deps
        self._budget = budget
        self._price = info.price
        self._context_limit = int(info.context_tokens * TRIM_AT)
        self._tools: list[ToolSpec] = deps.tools.describe(
            run.spec.tools, ToolContext(run.run_id, run.run_token)
        )
        self._validators = {t.name: Draft202012Validator(t.input_schema) for t in self._tools}
        self._specs = {t.name: t for t in self._tools}
        # Full history (what checkpoints hold); a trimmed copy is what the model sees.
        self._history: list[Message] = [Message.user(_initial_prompt(run.input)), *resume.messages]
        self._unsaved: list[Message] = []  # added since the last checkpoint
        self._last_turn = resume.last_turn
        self._sent: list[Message] = self._history
        # System prompt and tool schemas are sent with every call.
        self._fixed_chars = len(run.spec.instructions) + sum(
            len(t.name) + len(t.description) + len(json.dumps(t.input_schema)) for t in self._tools
        )
        self._tokens_per_char = DEFAULT_TOKENS_PER_CHAR
        self._outcome = ""  # of the latest tool call, for its span

    def run(self) -> RunUpdate:
        if self._last_turn is not None:  # resumed: finish what the latest saved turn asked for
            turn, stop = self._last_turn
            outcome = self._after_turn(turn, stop)
            if outcome is not None:
                return outcome
        while True:
            self._before_step()
            self._fit_context()
            attributes = {
                "run.step": self._budget.steps + 1,
                "gen_ai.request.model": self._spec.model,
            }
            with self._deps.tracer.span("model.call", attributes) as span:
                turn = self._deps.model.complete(
                    self._spec.model, self._spec.instructions, self._sent, self._tools
                )
                cost = self._price.cost_usd(turn.usage)
                span.set("gen_ai.usage.input_tokens", turn.usage.input_tokens)
                span.set("gen_ai.usage.output_tokens", turn.usage.output_tokens)
                span.set("cost.usd", round(cost, 6))
                span.set("stop_reason", turn.stop_reason.value)
            self._learn_token_ratio(turn.usage.input_tokens)
            self._budget.record_step(cost)
            message = Message.assistant(turn)
            self._add(message)
            self._save(cost, turn.stop_reason)
            outcome = self._after_turn(message, turn.stop_reason)
            if outcome is not None:
                return outcome

    def _after_turn(self, turn: Message, stop: StopReason) -> RunUpdate | None:
        """Acts on a model turn: the run's outcome, or None to ask the model again. Tool calls
        that already have a result in the history (a resumed run) are skipped."""
        if stop is StopReason.REFUSAL:
            return self._fail("The model declined the request")
        if stop is StopReason.MAX_TOKENS:
            return self._fail("The model reply was cut off (max tokens)")
        if not turn.tool_calls:
            return RunUpdate(
                RunStatus.SUCCEEDED,
                steps=self._budget.steps,
                cost_usd=_cost(self._budget),
                output=turn.text,
            )
        # Results after this turn only: some providers reuse call ids across turns.
        start = max(i for i, m in enumerate(self._history) if m is turn)
        answered = {
            m.tool_result.call_id for m in self._history[start:] if m.tool_result is not None
        }
        for call in turn.tool_calls:
            if call.id in answered:
                continue
            rejection = self._rejection(call)
            if rejection is not None:
                log.warning("run %s: rejected %s: %s", self._run.run_id, call.name, rejection)
                # Not a step: saved with the next checkpoint, or decided again on resume.
                self._add(Message.tool(ToolResult(call.id, rejection, is_error=True)))
            elif call.name in self._spec.require_approval and not self._approved(call):
                return self._pause_for_approval(call)
            else:
                self._add(Message.tool(self._execute(call)))
                self._save(0.0)
        return None

    def _add(self, message: Message) -> None:
        self._history.append(message)
        self._unsaved.append(message)

    def _save(self, cost_usd: float, stop: StopReason | None = None) -> None:
        """Checkpoints the step just recorded (step number = steps so far)."""
        if self._attempt is not None and self._deps.state is not None:
            data: dict[str, Any] = {
                "messages": [m.to_json() for m in self._unsaved],
                "cost_usd": cost_usd,
                "elapsed_s": round(self._budget.elapsed(), 3),
            }
            if stop is not None:
                data["stop_reason"] = stop.value
            with self._deps.tracer.span("checkpoint", {"run.step": self._budget.steps}):
                self._deps.state.save_checkpoint(
                    self._run.run_id, self._attempt.owner, self._budget.steps, data
                )
        self._unsaved.clear()

    def _estimate(self, messages: Sequence[Message]) -> int:
        chars = self._fixed_chars + sum(message_chars(m) for m in messages)
        return int(chars * self._tokens_per_char)

    def _fit_context(self) -> None:
        """Sets what the next call sends: the history, or a trimmed copy if it is too long."""
        fitted = fit_context(self._history, self._context_limit, self._estimate)
        if fitted != self._history:
            log.info(
                "run %s: trimmed conversation to fit the context window (%d -> %d messages)",
                self._run.run_id,
                len(self._history),
                len(fitted),
            )
        self._sent = fitted

    def _learn_token_ratio(self, input_tokens: int) -> None:
        """Calibrates the estimate with the tokens the provider counted for what we sent."""
        chars = self._fixed_chars + sum(message_chars(m) for m in self._sent)
        if input_tokens > 0 and chars > 0:
            self._tokens_per_char = input_tokens / chars

    def _before_step(self) -> None:
        if self._attempt is not None and self._attempt.cancelled():
            raise LeaseLost(f"Run {self._run.run_id}: lease lost")
        self._budget.check()

    def _execute(self, call: ToolCall) -> ToolResult:
        """Runs an allowed, well-formed call. A tool failure becomes an error result.

        With a run-state store: claim the call's key, call the tool, store the result. A key
        claimed before (a resumed run) returns its stored result without calling again."""
        self._before_step()
        attributes = {"run.step": self._budget.steps + 1, "gen_ai.tool.name": call.name}
        with self._deps.tracer.span("tool.call", attributes) as span:
            result = self._execute_claimed(call)
            span.set("tool.outcome", self._outcome)
        return result

    def _execute_claimed(self, call: ToolCall) -> ToolResult:
        key = self._claim(call)
        if isinstance(key, ToolResult):
            self._outcome = "reused"
            self._budget.record_step()
            return key
        step = self._budget.steps + 1
        context = ToolContext(
            self._run.run_id,
            self._run.run_token,
            step,
            tool_call_key(self._run.run_id, step, call),
        )
        try:
            content = self._deps.tools.call(call.name, call.arguments, context)
            result = ToolResult(call.id, content)
        except ToolError as e:
            result = ToolResult(call.id, f"Tool failed: {e}", is_error=True)
        self._outcome = "error" if result.is_error else "ok"
        if key is not None:
            assert self._deps.state is not None and self._attempt is not None
            self._deps.state.complete_tool_call(
                self._run.run_id,
                self._attempt.owner,
                key,
                {"content": result.content, "is_error": result.is_error},
            )
        self._budget.record_step()
        return result

    def _claim(self, call: ToolCall) -> str | ToolResult | None:
        """The key to store the result under, the stored result of an earlier attempt, or None
        when nothing is saved (no store or attempt)."""
        state, attempt = self._deps.state, self._attempt
        if state is None or attempt is None:
            return None
        run_id = self._run.run_id
        key = tool_call_key(run_id, self._budget.steps + 1, call)
        claim = state.claim_tool_call(run_id, attempt.owner, key)
        if claim.result is not None:
            log.info("run %s: %s already ran; reusing its result", run_id, call.name)
            return ToolResult(call.id, claim.result["content"], claim.result["is_error"])
        if not claim.fresh:
            if not self._specs[call.name].safe_to_repeat:
                raise ToolOutcomeUnknown(
                    f"Outcome unknown: {call.name} may have run before a worker crash; "
                    "not repeated because it is neither read-only nor idempotent"
                )
            log.warning("run %s: repeating %s (outcome unknown, safe to repeat)", run_id, call.name)
        return key

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

    def _approved(self, call: ToolCall) -> bool:
        """True if the run's decision approves exactly this call at this step."""
        approval = self._run.approval
        if approval is None:
            return False
        matches = (
            approval.step == self._budget.steps + 1
            and approval.tool == call.name
            and _canonical(approval.arguments) == _canonical(call.arguments)
        )
        if not matches:
            return False  # a decision on an earlier pause
        if approval.decision is not Decision.APPROVED:
            log.warning(
                "run %s: %s was rejected but the run was queued; pausing again",
                self._run.run_id,
                call.name,
            )
            return False
        log.info("run %s: %s approved by %s", self._run.run_id, call.name, approval.decided_by)
        return True

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


def _canonical(value: Any) -> str:
    """JSON text that is equal only for equal JSON values (``true`` is not ``1``)."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _initial_prompt(run_input: dict[str, Any]) -> str:
    return "Run input (JSON):\n" + json.dumps(run_input, indent=2, sort_keys=True)
