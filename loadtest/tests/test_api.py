import asyncio
import json

import httpx
import pytest

from loadtest.api import Api, ApiError


def make_api(handler, attempts=3):
    sleeps = []

    async def sleep(seconds):
        sleeps.append(seconds)

    client = httpx.AsyncClient(base_url="https://api.test", transport=httpx.MockTransport(handler))
    return Api(client, "secret-key", attempts=attempts, backoff_s=0.5, sleep=sleep), sleeps


def test_start_run_sends_key_and_body():
    seen = {}

    def handler(request: httpx.Request):
        seen["key"] = request.headers["X-Api-Key"]
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={"run_id": "run_1", "status": "QUEUED"})

    api, _ = make_api(handler)
    run = asyncio.run(api.start_run("load-stub", {"a": 1}))
    assert run["run_id"] == "run_1"
    assert seen == {
        "key": "secret-key",
        "path": "/runs",
        "body": {"agent": "load-stub", "input": {"a": 1}},
    }


def test_publish_sends_yaml():
    def handler(request: httpx.Request):
        assert request.headers["Content-Type"] == "application/yaml"
        assert request.url.path == "/agents/load-stub/versions"
        assert request.content == b"name: load-stub\n"
        return httpx.Response(201, json={"version": 3})

    api, _ = make_api(handler)
    assert asyncio.run(api.publish("load-stub", b"name: load-stub\n")) == 3


def test_create_agent_accepts_conflict():
    api, _ = make_api(lambda r: httpx.Response(409, json={"error": "exists"}))
    assert asyncio.run(api.create_agent("load-stub")) is False
    api, _ = make_api(lambda r: httpx.Response(201, json={}))
    assert asyncio.run(api.create_agent("load-stub")) is True


def test_retries_throttle_and_lambda_limit_then_succeeds():
    answers = iter(
        [httpx.Response(429), httpx.Response(503), httpx.Response(200, json={"status": "RUNNING"})]
    )
    api, sleeps = make_api(lambda r: next(answers))
    assert asyncio.run(api.get_run("run_1")) == {"status": "RUNNING"}
    assert sleeps == [0.5, 1.0]
    assert api.retries.by_status == {429: 1, 503: 1}


def test_gives_up_after_attempts():
    api, sleeps = make_api(lambda r: httpx.Response(429, text="slow down"), attempts=2)
    with pytest.raises(ApiError) as caught:
        asyncio.run(api.get_run("run_1"))
    assert caught.value.status == 429
    assert sleeps == [0.5]
    assert api.retries.by_status == {429: 2}


def test_other_errors_are_not_retried():
    api, sleeps = make_api(lambda r: httpx.Response(404, text="no run"))
    with pytest.raises(ApiError) as caught:
        asyncio.run(api.get_run("run_x"))
    assert caught.value.status == 404
    assert sleeps == []


def test_error_message_has_no_key():
    api, _ = make_api(lambda r: httpx.Response(400, text="bad"))
    with pytest.raises(ApiError) as caught:
        asyncio.run(api.start_run("load-stub", {}))
    assert "secret-key" not in str(caught.value)


def test_over_the_daily_spend_cap_is_not_retried():
    calls = []

    def handler(request: httpx.Request):
        calls.append(request.url.path)
        return httpx.Response(429, json={"status": 429, "reason": "tenant_budget"})

    api, sleeps = make_api(handler)
    with pytest.raises(ApiError) as refused:
        asyncio.run(api.start_run("load-stub", {}))

    assert (refused.value.status, refused.value.reason) == (429, "tenant_budget")
    assert calls == ["/runs"] and sleeps == []
    assert api.retries.by_status == {}


def test_plain_429_throttling_is_still_retried():
    answers = iter([httpx.Response(429, json={"message": "Too Many Requests"})])

    def handler(request: httpx.Request):
        return next(answers, httpx.Response(201, json={"run_id": "run_1"}))

    api, sleeps = make_api(handler)
    assert asyncio.run(api.start_run("load-stub", {}))["run_id"] == "run_1"
    assert api.retries.by_status == {429: 1} and sleeps == [0.5]
