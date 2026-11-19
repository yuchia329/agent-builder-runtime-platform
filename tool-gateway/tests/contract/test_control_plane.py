"""ControlPlane contract: every adapter passes. The HTTP adapter runs against the fake control
plane (tests/fake_control_plane.py)."""

import pytest
from fake_control_plane import FakeControlPlane

from gateway.adapters.control_plane_http import HttpControlPlane
from gateway.adapters.control_plane_memory import MemoryControlPlane
from gateway.ports.control_plane import ControlPlaneUnavailable, RunNotFound

RUN = {"run_id": "run_a/b", "tenant_id": "tenant-a", "status": "RUNNING", "run_token": "eyJ..."}


class Memory:
    def __enter__(self):
        self.adapter = MemoryControlPlane()
        return self

    def __exit__(self, *exc):
        pass

    def put(self, run):
        self.adapter.put(run)

    def break_it(self):
        self.adapter.unavailable = True


class Http:
    def __enter__(self):
        self.server = FakeControlPlane().__enter__()
        self.adapter = HttpControlPlane(self.server.url, sleep=lambda _: None)
        return self

    def __exit__(self, *exc):
        self.server.__exit__(*exc)

    def put(self, run):
        self.server.put(run)

    def break_it(self):
        self.server.fail_next(503, times=3)


ADAPTERS = [pytest.param(Memory, id="memory"), pytest.param(Http, id="http")]


@pytest.fixture(params=ADAPTERS)
def plane(request):
    with request.param() as p:
        yield p


def test_returns_the_run(plane):
    plane.put(RUN)

    assert plane.adapter.get_run("run_a/b") == RUN


def test_missing_run_is_run_not_found(plane):
    with pytest.raises(RunNotFound):
        plane.adapter.get_run("run_missing")


def test_failure_is_control_plane_unavailable(plane):
    plane.put(RUN)
    plane.break_it()

    with pytest.raises(ControlPlaneUnavailable):
        plane.adapter.get_run("run_a/b")


# HTTP adapter only.


def test_http_retries_server_errors():
    with FakeControlPlane() as server:
        server.put(RUN)
        server.fail_next(502, times=2)

        assert HttpControlPlane(server.url, sleep=lambda _: None).get_run("run_a/b") == RUN
        assert len(server.requests) == 3


@pytest.mark.parametrize("status", [401, 403, 409])
def test_http_other_statuses_name_the_status_and_are_not_retried(status):
    with FakeControlPlane() as server:
        server.fail_next(status)

        with pytest.raises(ControlPlaneUnavailable, match=f"answered {status}"):
            HttpControlPlane(server.url, sleep=lambda _: None).get_run("run_a/b")
        assert len(server.requests) == 1


def test_http_body_that_is_not_json_is_unavailable():
    with FakeControlPlane() as server:
        server.garbage = True

        with pytest.raises(ControlPlaneUnavailable, match="not JSON"):
            HttpControlPlane(server.url, sleep=lambda _: None).get_run("run_a/b")


def test_http_unreachable_is_unavailable():
    with FakeControlPlane() as server:
        url = server.url

    with pytest.raises(ControlPlaneUnavailable, match="unreachable"):
        HttpControlPlane(url, sleep=lambda _: None).get_run("run_a/b")


def test_http_quotes_the_run_id_and_uses_the_signer():
    def signer(method, url, headers, body):
        assert (method, body) == ("GET", b"")
        return {**headers, "Authorization": "signed"}

    with FakeControlPlane() as server:
        server.put(RUN)
        HttpControlPlane(server.url, signer=signer).get_run("run_a/b")

        path, headers = server.requests[0]
        assert path == "/internal/runs/run_a%2Fb"
        assert headers["Authorization"] == "signed"
