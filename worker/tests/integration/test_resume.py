"""Checkpoints and resume (ADR 0011): a run stopped after step k and handled again by another
worker continues from step k+1, with the same final steps and cost as a run that never stopped.

A worker "dies" by raising Killed, a BaseException that handle_run does not catch, at the point
under test (a checkpoint write, a status report), like a process killed there.
"""

import pytest
from contracts import contract_errors, example_run

from worker.adapters.control_plane_memory import InMemoryControlPlane
from worker.adapters.model_stub import ScriptedModel, final_turn, tool_turn
from worker.adapters.run_state_memory import InMemoryRunStateStore
from worker.adapters.tools_fake import issue_triage_tools
from worker.core.errors import LeaseLost, StateError
from worker.core.handle_run import Attempt, Deps, handle_run
from worker.core.types import RunStatus

RUN = example_run()
RUN_ID = RUN["run_id"]
REPO = "acme/shop"
SEARCH = ("github.search_issues", {"repo": REPO, "query": "refund EUR"})
LABEL = ("github.add_label", {"repo": REPO, "issue_number": 42, "label": "bug"})
LEASE = 600  # longer than any fake tool delay below
TURN_COST = 0.000054  # scripted turns: 100 input + 20 output tokens on minimax/MiniMax-M3


class Killed(BaseException):
    """The worker process dies here."""


class DyingStore(InMemoryRunStateStore):
    """Dies just before saving step `die_at` (the step itself already happened)."""

    die_at: int | None = None

    def save_checkpoint(self, run_id, owner, step, data):
        if step == self.die_at:
            raise Killed(f"killed before saving step {step}")
        super().save_checkpoint(run_id, owner, step, data)


class DyingControlPlane(InMemoryControlPlane):
    die_on: str | None = None  # a status

    def update_run(self, run_id, update):
        if update.status == self.die_on:
            raise Killed(f"killed before reporting {update.status}")
        super().update_run(run_id, update)


class Platform:
    """Shared state (control plane, run-state store) and one worker attempt at a time."""

    def __init__(self, clock, **budget):
        self.clock = clock
        self.state = DyingStore(clock)
        self.control_plane = DyingControlPlane()
        spec = RUN["spec"] | {"model": "minimax/MiniMax-M3"}
        spec["budget"] = spec["budget"] | budget
        self.control_plane.add_run(example_run() | {"spec": spec})
        self.attempts = 0

    def attempt(self, turns, *, tools=None, cancel_after=None):
        """One worker takes the lease and handles the run. Returns (status or exception, model,
        tools). `cancel_after`: the lease is lost after that many step checks."""
        self.attempts += 1
        owner = f"worker-{self.attempts}/1"
        assert self.state.take_lease(RUN_ID, owner, LEASE) is not None
        model, tools = ScriptedModel(turns), tools or issue_triage_tools()
        checks = []

        def cancelled():
            checks.append(1)
            return cancel_after is not None and len(checks) > cancel_after

        deps = Deps(self.control_plane, model, tools, self.clock, state=self.state)
        try:
            outcome = handle_run(RUN_ID, deps, Attempt(owner, cancelled))
        except (Killed, LeaseLost, StateError) as e:
            outcome = e
        self.clock.advance(LEASE + 1)  # the lease runs out; the next worker may take it
        return outcome, model, tools

    @property
    def stored(self):
        return self.control_plane.run_json(RUN_ID)

    def statuses(self):
        return [body["status"] for _, body in self.control_plane.updates]


FULL = [tool_turn(SEARCH), tool_turn(LABEL), final_turn("Labeled bug; no duplicate.")]


def test_reference_run_without_interruption(clock):
    platform = Platform(clock)

    outcome, model, tools = platform.attempt(FULL)

    assert outcome is RunStatus.SUCCEEDED
    assert platform.stored["steps"] == 5
    assert platform.stored["cost_usd"] == pytest.approx(3 * TURN_COST)
    assert len(platform.state.load_checkpoints(RUN_ID)) == 5


def test_run_stopped_after_step_k_resumes_without_repeating_steps(clock):
    platform = Platform(clock)

    first, model_1, tools_1 = platform.attempt(FULL, cancel_after=2)  # model turn + search
    second, model_2, tools_2 = platform.attempt(FULL[1:])

    assert isinstance(first, LeaseLost)
    assert second is RunStatus.SUCCEEDED
    assert len(model_1.calls) == 1 and len(model_2.calls) == 2
    assert [n for n, _ in tools_1.calls] == ["github.search_issues"]
    assert [n for n, _ in tools_2.calls] == ["github.add_label"]
    # The resumed model sees the history: input, its search request, the search result.
    resumed = model_2.calls[0].messages
    assert [m.role for m in resumed] == ["user", "assistant", "tool"]
    assert resumed[1].tool_calls[0].name == "github.search_issues"
    # Same totals as the reference run.
    assert platform.stored["steps"] == 5
    assert platform.stored["cost_usd"] == pytest.approx(3 * TURN_COST)
    assert platform.statuses() == ["RUNNING", "RUNNING", "SUCCEEDED"]
    assert platform.control_plane.updates[1][1] == {"status": "RUNNING", "steps": 2}
    for _, body in platform.control_plane.updates:
        assert contract_errors("RunUpdate", body) == [], body


def test_crash_between_a_tool_call_and_its_checkpoint_runs_only_that_call_again(clock):
    platform = Platform(clock)
    platform.state.die_at = 2  # search ran, its checkpoint was not written

    first, _, tools_1 = platform.attempt(FULL)
    platform.state.die_at = None
    second, model_2, tools_2 = platform.attempt(FULL[1:])

    assert isinstance(first, Killed)
    assert second is RunStatus.SUCCEEDED
    assert len(model_2.calls) == 2  # the first model turn was saved: not asked again
    # The unsaved call runs again; idempotency keys (next M2 task) will stop this repeat.
    assert [n for n, _ in tools_2.calls] == ["github.search_issues", "github.add_label"]
    assert platform.stored["steps"] == 5


def test_resume_mid_turn_runs_only_the_calls_without_a_saved_result(clock):
    both = tool_turn(SEARCH, LABEL)
    platform = Platform(clock)
    platform.state.die_at = 3  # step 1 turn, step 2 search saved; label ran, not saved

    platform.attempt([both, final_turn("done")])
    platform.state.die_at = None
    second, model_2, tools_2 = platform.attempt([final_turn("done")])

    assert second is RunStatus.SUCCEEDED
    assert [n for n, _ in tools_2.calls] == ["github.add_label"]
    results = [m.tool_result.call_id for m in model_2.calls[0].messages if m.tool_result]
    assert results == [c.id for c in both.tool_calls]  # one result per call, in call order
    assert platform.stored["steps"] == 4


def test_rejected_call_is_decided_again_on_resume_without_duplicates(clock):
    turn = tool_turn(("github.delete_repo", {"repo": REPO}), SEARCH)
    platform = Platform(clock)
    platform.state.die_at = 2  # the rejection is not a step; search ran, not saved

    platform.attempt([turn, final_turn("done")])
    platform.state.die_at = None
    second, model_2, _ = platform.attempt([final_turn("done")])

    assert second is RunStatus.SUCCEEDED
    results = [m.tool_result for m in model_2.calls[0].messages if m.tool_result]
    assert [r.call_id for r in results] == [c.id for c in turn.tool_calls]
    assert results[0].is_error and "not allowed" in results[0].content


def test_final_answer_saved_but_not_reported_is_reported_without_asking_again(clock):
    platform = Platform(clock)
    platform.control_plane.die_on = "SUCCEEDED"

    first, _, _ = platform.attempt(FULL)
    platform.control_plane.die_on = None
    second, model_2, tools_2 = platform.attempt([])

    assert isinstance(first, Killed)
    assert second is RunStatus.SUCCEEDED
    assert model_2.calls == [] and tools_2.calls == []
    assert platform.stored["output"] == "Labeled bug; no duplicate."
    assert platform.stored["steps"] == 5
    assert platform.stored["cost_usd"] == pytest.approx(3 * TURN_COST)


def test_time_budget_counts_working_time_across_attempts_not_the_gap(clock):
    class SlowTools:
        def __init__(self):
            self.inner, self.calls = issue_triage_tools(), []

        def describe(self, names):
            return self.inner.describe(names)

        def call(self, name, arguments):
            self.calls.append(name)
            clock.advance(100)  # each tool call takes 100 working seconds
            return self.inner.call(name, arguments)

    platform = Platform(clock, max_seconds=180)

    platform.attempt(FULL, tools=SlowTools(), cancel_after=2)  # 100 s of work
    clock.advance(10_000)  # the run waits in the queue: not counted
    second, _, tools_2 = platform.attempt(FULL[1:], tools=SlowTools())

    assert second is RunStatus.FAILED
    assert platform.stored["failure"]["reason"] == "budget_seconds"  # 100 + 100 >= 180
    assert tools_2.calls == ["github.add_label"]


def test_checkpoint_after_losing_the_lease_stops_without_reporting(clock):
    platform = Platform(clock)

    class Thief:
        """Tools that stall past the lease; another worker takes the run meanwhile."""

        def __init__(self):
            self.inner, self.calls = issue_triage_tools(), []

        def describe(self, names):
            return self.inner.describe(names)

        def call(self, name, arguments):
            self.calls.append(name)
            clock.advance(LEASE + 1)
            platform.state.take_lease(RUN_ID, "worker-other/1", LEASE)
            return self.inner.call(name, arguments)

    outcome, _, _ = platform.attempt(FULL, tools=Thief())

    assert isinstance(outcome, LeaseLost)
    assert platform.statuses() == ["RUNNING"]
    assert len(platform.state.load_checkpoints(RUN_ID)) == 1  # the model turn only


def test_store_failure_stops_the_attempt_without_failing_the_run(clock):
    platform = Platform(clock)

    def broken(run_id, owner, step, data):
        raise StateError("DynamoDB unreachable")

    platform.state.save_checkpoint = broken

    outcome, _, _ = platform.attempt(FULL)

    assert isinstance(outcome, StateError)
    assert platform.statuses() == ["RUNNING"]  # still RUNNING: it resumes later


def test_without_an_attempt_nothing_is_saved(clock):
    platform = Platform(clock)
    deps = Deps(
        platform.control_plane,
        ScriptedModel(FULL),
        issue_triage_tools(),
        clock,
        state=platform.state,
    )

    assert handle_run(RUN_ID, deps) is RunStatus.SUCCEEDED
    assert platform.state.load_checkpoints(RUN_ID) == []
