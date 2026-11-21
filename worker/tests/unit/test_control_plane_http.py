"""HttpControlPlane details beyond the ControlPlane contract: signing, retries, error mapping."""

import json

import pytest
from contracts import CONTRACTS, contract_errors, example_run
from fake_control_plane_server import deferred_problem, fake_signer, http_client

from worker.adapters.control_plane_http import HttpControlPlane
from worker.core.errors import ControlPlaneError, RunDeferred
from worker.core.types import RunStatus, RunUpdate

RUN_ID = example_run()["run_id"]


@pytest.fixture
def server(fake_control_plane_server):
    fake_control_plane_server.state.add_run(example_run())
    return fake_control_plane_server


def test_requests_are_sigv4_signed_for_execute_api(server):
    http_client(server).get_run(RUN_ID)

    _, path, headers = server.requests[-1]
    assert path == f"/internal/runs/{RUN_ID}"
    assert "/us-east-1/execute-api/aws4_request" in headers["Authorization"]
    assert headers["X-Amz-Date"]


def test_requests_carry_the_current_trace(server):
    trace_parent = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    http_client(server, trace_headers=lambda: {"traceparent": trace_parent}).get_run(RUN_ID)

    _, _, headers = server.requests[-1]
    assert headers["traceparent"] == trace_parent


def test_patch_body_matches_contract(server):
    http_client(server).update_run(RUN_ID, RunUpdate(RunStatus.RUNNING, steps=0))

    assert server.state.updates == [(RUN_ID, {"status": "RUNNING", "steps": 0})]


def test_retries_5xx_then_succeeds(server):
    server.fail_next(503, times=2)

    run = http_client(server, attempts=3).get_run(RUN_ID)

    assert run.run_id == RUN_ID
    assert len(server.requests) == 3


def test_gives_up_after_attempts(server):
    server.fail_next(500, times=3)

    with pytest.raises(ControlPlaneError, match="after 3 attempts.*HTTP 500"):
        http_client(server, attempts=3).get_run(RUN_ID)


def test_unreachable_control_plane_raises_after_retries():
    sleeps = []
    cp = HttpControlPlane("http://127.0.0.1:9", fake_signer(), sleep=sleeps.append, attempts=3)

    with pytest.raises(ControlPlaneError, match="after 3 attempts"):
        cp.get_run(RUN_ID)
    assert sleeps == [0.5, 1.0]


def test_client_error_is_not_retried(server):
    server.fail_next(400)

    with pytest.raises(ControlPlaneError, match="400"):
        http_client(server).get_run(RUN_ID)
    assert len(server.requests) == 1


def test_fake_server_problems_and_summaries_match_contract(server):
    assert contract_errors("DeferredProblem", deferred_problem(30)) == []
    example = json.loads((CONTRACTS / "examples" / "deferred-problem.valid.json").read_text())
    assert deferred_problem(30).keys() == example.keys()
    assert contract_errors("RunSummary", server.summary(RUN_ID)) == []


def test_deferred_start_is_not_retried(server):
    server.state.max_running = 0  # every start is put off

    with pytest.raises(RunDeferred) as deferred:
        http_client(server, attempts=3).update_run(RUN_ID, RunUpdate(RunStatus.RUNNING))

    assert deferred.value.retry_after_seconds == 30
    assert len(server.requests) == 1


def test_plain_429_throttling_is_still_retried(server):
    server.fail_next(429, times=2)  # API Gateway throttling: problem type about:blank

    http_client(server, attempts=3).update_run(RUN_ID, RunUpdate(RunStatus.RUNNING))

    assert len(server.requests) == 3
    assert server.state.run_json(RUN_ID)["status"] == "RUNNING"


def test_defer_posts_a_contract_body(server):
    cp = http_client(server)
    cp.update_run(RUN_ID, RunUpdate(RunStatus.RUNNING))

    assert cp.defer_run(RUN_ID, 45) is RunStatus.QUEUED

    method, path, _ = server.requests[-1]
    assert (method, path) == ("POST", f"/internal/runs/{RUN_ID}/defer")
    assert server.state.deferrals == [
        (RUN_ID, {"reason": "rate_limited", "retry_after_seconds": 45})
    ]
