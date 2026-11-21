"""Tenant limits as the worker meets them (ADR 0026, ADR 0027), with in-memory adapters and once
over HTTP against the fake control plane server:

- a start put off at the tenant's cap ends the attempt with the run QUEUED, nothing executed;
- a start refused over the daily spend cap ends with the run FAILED (tenant_budget);
- a model still rate limited after the adapter's retries sets the run aside (defer), and the run
  resumes later from its checkpoints; after 15 minutes set aside it fails rate_limited.

In every case the poll loop deletes the message and releases the lease.
"""

import json

import pytest
from contracts import contract_errors, example_run
from fake_control_plane_server import http_client

from worker.adapters.clock_system import SystemClock
from worker.adapters.control_plane_memory import InMemoryControlPlane
from worker.adapters.model_stub import ScriptedModel, StubModel, demo_turns, final_turn, tool_turn
from worker.adapters.queue_memory import InMemoryRunQueue
from worker.adapters.run_state_memory import InMemoryRunStateStore
from worker.adapters.tools_fake import issue_triage_tools
from worker.core.errors import ModelRateLimited
from worker.core.handle_run import Attempt, Deps, handle_run, set_aside_delay
from worker.core.types import RunStatus, RunUpdate
from worker.entrypoints.poll_loop import PollLoop

RUN = example_run()
RUN_ID = RUN["run_id"]
OTHER_ID = "run_other"
REPO = "acme/payments"
SEARCH = ("github.search_issues", {"repo": REPO, "query": "refund EUR"})
LABEL = ("github.add_label", {"repo": REPO, "issue_number": 42, "label": "bug"})


class RateLimitedAt(ScriptedModel):
    """Plays the script, but its call number `at` (1-based) raises ModelRateLimited."""

    def __init__(self, turns, at, retry_after=None):
        super().__init__(turns)
        self._at, self._retry_after = at, retry_after

    def complete(self, model, system, messages, tools):
        if len(self.calls) + 1 == self._at:
            self.calls.append(None)
            raise ModelRateLimited("HTTP 429 (gave up after 4 attempts)", self._retry_after)
        return super().complete(model, system, messages, tools)


def _control_plane(**limits):
    cp = InMemoryControlPlane()
    cp.add_run(example_run())
    cp.add_run(example_run() | {"run_id": OTHER_ID})
    for name, value in limits.items():
        setattr(cp, name, value)
    return cp


def _handle(cp, model, clock, state=None, owner="worker-a/1"):
    deps = Deps(cp, model, issue_triage_tools(), clock, state=state)
    if state is not None:
        assert state.take_lease(RUN_ID, owner, 600) is not None
    return handle_run(RUN_ID, deps, None if state is None else Attempt(owner, lambda: False))


def test_start_at_the_cap_leaves_the_run_queued_and_runs_nothing(clock):
    cp = _control_plane(max_running=1)
    cp.update_run(OTHER_ID, RunUpdate(RunStatus.RUNNING))
    model = ScriptedModel([final_turn("never")])

    assert _handle(cp, model, clock) is RunStatus.QUEUED

    assert model.calls == []
    assert cp.run_json(RUN_ID)["status"] == "QUEUED"
    assert [run for run, _ in cp.updates] == [OTHER_ID]


def test_start_over_the_daily_spend_cap_ends_failed_and_runs_nothing(clock):
    cp = _control_plane()
    cp.over_budget.add(RUN["tenant_id"])
    model = ScriptedModel([final_turn("never")])

    assert _handle(cp, model, clock) is RunStatus.FAILED

    assert model.calls == []
    assert cp.run_json(RUN_ID)["failure"]["reason"] == "tenant_budget"


def test_rate_limited_model_sets_the_run_aside_and_it_resumes_from_its_checkpoints(clock):
    cp = _control_plane()
    state = InMemoryRunStateStore(clock)
    turns = [tool_turn(SEARCH), tool_turn(LABEL), final_turn("Labeled bug.")]

    first = RateLimitedAt(turns, at=2, retry_after=120)
    assert _handle(cp, first, clock, state) is RunStatus.QUEUED

    assert cp.deferrals == [(RUN_ID, {"reason": "rate_limited", "retry_after_seconds": 120})]
    progress = cp.updates[-1][1]
    assert progress["status"] == "RUNNING" and progress["steps"] == 2  # model turn + tool call
    for _, body in cp.updates:
        assert contract_errors("RunUpdate", body) == [], body
    assert contract_errors("RunDefer", cp.deferrals[0][1]) == []
    state.release_lease(RUN_ID, "worker-a/1")

    second = ScriptedModel(turns[1:])
    assert _handle(cp, second, clock, state, owner="worker-b/1") is RunStatus.SUCCEEDED

    assert len(second.calls) == 2  # the search was not repeated
    assert cp.run_json(RUN_ID)["steps"] == 5


def test_after_fifteen_minutes_set_aside_the_run_fails_rate_limited(clock):
    cp = _control_plane()
    for _ in range(2):  # 900 s set aside, then one more try
        model = RateLimitedAt([final_turn("never")], at=1, retry_after=900)
        status = _handle(cp, model, clock)

    assert status is RunStatus.FAILED
    assert cp.run_json(RUN_ID)["failure"]["reason"] == "rate_limited"
    assert cp.updates[-1][1]["status"] == "RUNNING"  # the worker never reports the failure itself


def test_set_aside_delay_follows_the_provider_or_spreads_around_30_seconds():
    assert set_aside_delay(120) == 120
    assert set_aside_delay(0.2) == 1
    assert set_aside_delay(0) == 1
    assert set_aside_delay(5000) == 900
    assert set_aside_delay(None, lambda low, high: low) == 24
    assert set_aside_delay(None, lambda low, high: high) == 36
    assert all(24 <= set_aside_delay(None) <= 36 for _ in range(200))


def _body(run_id=RUN_ID):
    return json.dumps({"schema_version": 1, "run_id": run_id, "tenant_id": RUN["tenant_id"]})


@pytest.mark.parametrize("case", ["deferred_start", "rate_limited_model"])
def test_poll_loop_deletes_the_message_and_releases_the_lease(fake_control_plane_server, case):
    server = fake_control_plane_server
    server.state.add_run(example_run())
    model = StubModel(demo_turns())
    if case == "deferred_start":
        server.state.max_running = 0
    else:
        model = StubModel(demo_turns(), rate_limited_share=1.0)
    state = InMemoryRunStateStore(SystemClock())
    queue = InMemoryRunQueue()
    queue.send(_body())
    deps = Deps(http_client(server), model, issue_triage_tools(), SystemClock(), state=state)

    loop = PollLoop(queue, deps, state, wait_seconds=0)
    loop.poll_once()
    loop.drain()

    assert server.state.run_json(RUN_ID)["status"] == "QUEUED"
    assert queue.pending() == 0
    assert state.take_lease(RUN_ID, "next-worker/1", 30) is not None
    if case == "rate_limited_model":
        assert [body["reason"] for _, body in server.state.deferrals] == ["rate_limited"]
