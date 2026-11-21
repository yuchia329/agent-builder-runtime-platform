"""Behavior every ControlPlane adapter must have. The HTTP adapter runs against a local fake
server that checks requests against the contract (tests/fake_control_plane_server.py)."""

import pytest
from contracts import example_run
from fake_control_plane_server import FakeControlPlaneServer, http_client

from worker.adapters.control_plane_memory import InMemoryControlPlane
from worker.core.errors import RunConflict, RunDeferred, RunNotFound
from worker.core.types import Decision, Failure, FailureReason, RunStatus, RunUpdate

DECIDED_ID = "run_decided"
APPROVAL = {
    "step": 4,
    "tool": "github.close_issue",
    "arguments": {"repo": "acme/payments", "issue_number": 42},
    "decision": "approved",
    "decided_by": "api:tenant-demo",
    "decided_at": "2026-11-17T12:00:00Z",
}


def _decided_run():
    """A run the control plane queued again after its pending call was approved."""
    return example_run() | {"run_id": DECIDED_ID, "steps": 3, "approval": APPROVAL}


def _memory():
    cp = InMemoryControlPlane()
    cp.add_run(example_run())
    cp.add_run(_decided_run())
    return cp, example_run()["run_id"]


def _http():
    server = FakeControlPlaneServer().__enter__()
    server.state.add_run(example_run())
    server.state.add_run(_decided_run())
    return (http_client(server), example_run()["run_id"]), server.__exit__


ADAPTERS = {"memory": lambda: (_memory(), None), "http": _http}


@pytest.fixture(params=ADAPTERS.values(), ids=ADAPTERS.keys())
def control_plane(request):
    adapter, cleanup = request.param()
    yield adapter
    if cleanup:
        cleanup()


def test_get_returns_run_with_spec(control_plane):
    cp, run_id = control_plane

    run = cp.get_run(run_id)

    assert run.run_id == run_id
    assert run.status is RunStatus.QUEUED
    assert run.spec.name == "issue-triage"
    assert "github.close_issue" in run.spec.require_approval


def test_get_returns_the_approval_decision(control_plane):
    cp, _ = control_plane

    approval = cp.get_run(DECIDED_ID).approval

    assert approval is not None
    assert approval.decision is Decision.APPROVED
    assert (approval.step, approval.tool) == (4, "github.close_issue")
    assert approval.arguments == APPROVAL["arguments"]
    assert approval.decided_by == "api:tenant-demo"


def test_unknown_run_raises(control_plane):
    cp, _ = control_plane

    with pytest.raises(RunNotFound):
        cp.get_run("run_missing")
    with pytest.raises(RunNotFound):
        cp.update_run("run_missing", RunUpdate(RunStatus.RUNNING))


def test_update_is_visible_on_next_get(control_plane):
    cp, run_id = control_plane

    cp.update_run(run_id, RunUpdate(RunStatus.RUNNING, steps=2))

    run = cp.get_run(run_id)
    assert run.status is RunStatus.RUNNING
    assert run.steps == 2


def test_disallowed_status_change_raises_conflict(control_plane):
    cp, run_id = control_plane

    with pytest.raises(RunConflict):
        cp.update_run(run_id, RunUpdate(RunStatus.SUCCEEDED, output="skipped RUNNING"))

    cp.update_run(run_id, RunUpdate(RunStatus.RUNNING))
    cp.update_run(run_id, RunUpdate(RunStatus.FAILED, failure=Failure(FailureReason.ERROR, "x")))
    with pytest.raises(RunConflict):
        cp.update_run(run_id, RunUpdate(RunStatus.RUNNING))


# Tenant limits (ADR 0026, ADR 0027). `limited` gives the adapter and the state behind it.

OTHER_ID = "run_other"


def _limited_memory():
    cp = InMemoryControlPlane()
    return cp, cp, None


def _limited_http():
    server = FakeControlPlaneServer().__enter__()
    return http_client(server), server.state, server.__exit__


LIMITED = {"memory": _limited_memory, "http": _limited_http}


@pytest.fixture(params=LIMITED.values(), ids=LIMITED.keys())
def limited(request):
    cp, state, cleanup = request.param()
    state.add_run(example_run())
    state.add_run(example_run() | {"run_id": OTHER_ID})
    yield cp, state
    if cleanup:
        cleanup()


def test_start_at_the_tenant_cap_raises_deferred_and_leaves_the_run_queued(limited):
    cp, state = limited
    state.max_running = 1
    run_id = example_run()["run_id"]
    cp.update_run(OTHER_ID, RunUpdate(RunStatus.RUNNING))

    with pytest.raises(RunDeferred) as deferred:
        cp.update_run(run_id, RunUpdate(RunStatus.RUNNING))

    assert 1 <= deferred.value.retry_after_seconds <= 900
    assert cp.get_run(run_id).status is RunStatus.QUEUED


def test_start_over_the_daily_spend_cap_raises_conflict_and_fails_the_run(limited):
    cp, state = limited
    state.over_budget.add(example_run()["tenant_id"])
    run_id = example_run()["run_id"]

    with pytest.raises(RunConflict):
        cp.update_run(run_id, RunUpdate(RunStatus.RUNNING))

    assert cp.get_run(run_id).status is RunStatus.FAILED
    assert state.run_json(run_id)["failure"]["reason"] == "tenant_budget"


def test_defer_sets_a_running_run_aside(limited):
    cp, state = limited
    run_id = example_run()["run_id"]
    cp.update_run(run_id, RunUpdate(RunStatus.RUNNING))

    assert cp.defer_run(run_id, 60) is RunStatus.QUEUED

    assert cp.get_run(run_id).status is RunStatus.QUEUED
    assert state.deferrals == [(run_id, {"reason": "rate_limited", "retry_after_seconds": 60})]


def test_defer_after_fifteen_minutes_set_aside_fails_the_run(limited):
    cp, state = limited
    run_id = example_run()["run_id"]
    cp.update_run(run_id, RunUpdate(RunStatus.RUNNING))
    cp.defer_run(run_id, 900)
    cp.update_run(run_id, RunUpdate(RunStatus.RUNNING))

    assert cp.defer_run(run_id, 60) is RunStatus.FAILED

    assert state.run_json(run_id)["failure"]["reason"] == "rate_limited"


def test_defer_of_a_run_that_is_not_running_raises(limited):
    cp, _ = limited

    with pytest.raises(RunConflict):
        cp.defer_run(example_run()["run_id"], 30)
    with pytest.raises(RunNotFound):
        cp.defer_run("run_missing", 30)
