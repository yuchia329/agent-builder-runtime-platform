"""Behavior every ControlPlane adapter must have. The HTTP adapter runs against a local fake
server that checks requests against the contract (tests/fake_control_plane_server.py)."""

import pytest
from contracts import example_run
from fake_control_plane_server import FakeControlPlaneServer, http_client

from worker.adapters.control_plane_memory import InMemoryControlPlane
from worker.core.errors import RunConflict, RunNotFound
from worker.core.types import Failure, FailureReason, RunStatus, RunUpdate


def _memory():
    cp = InMemoryControlPlane()
    cp.add_run(example_run())
    return cp, example_run()["run_id"]


def _http():
    server = FakeControlPlaneServer().__enter__()
    server.state.add_run(example_run())
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
