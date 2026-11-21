import asyncio

import httpx
import pytest

from loadtest.api import ApiError
from loadtest.driver import Outcome, open_runs, poll_until_final, schedule, start_runs


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self):
        return self.now

    async def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds
        await asyncio.sleep(0)


class FakeApi:
    """Runs move QUEUED -> RUNNING -> SUCCEEDED on successive GETs, unless scripted."""

    def __init__(self, fail_starts=(), scripts=None):
        self.started = 0
        self.fail_starts = set(fail_starts)
        self.scripts = scripts or {}
        self.gets: list[str] = []

    async def start_run(self, agent, run_input):
        index = self.started
        self.started += 1
        await asyncio.sleep(0)
        if index in self.fail_starts:
            raise ApiError("POST", "/runs", 503, "")
        return {"run_id": f"run_{index}", "trace_id": f"t{index}", "status": "QUEUED"}

    async def get_run(self, run_id):
        self.gets.append(run_id)
        script = self.scripts.setdefault(run_id, ["RUNNING", "SUCCEEDED"])
        if not script:
            raise AssertionError(f"{run_id} polled after it was final")
        status = script.pop(0)
        if status == "error":
            raise ApiError("GET", f"/runs/{run_id}", 503, "")
        if status == "network":
            raise httpx.ReadTimeout("slow")
        return {"run_id": run_id, "status": status}


def test_schedule_spaces_runs_evenly():
    assert schedule(4, 2.0) == [0.0, 0.5, 1.0, 1.5]
    assert schedule(0, 1.0) == []
    with pytest.raises(ValueError):
        schedule(1, 0)


def test_start_runs_keeps_the_rate_and_collects_errors():
    clock = FakeClock()
    api = FakeApi(fail_starts={1})
    outcome = asyncio.run(start_runs(api, "load-stub", {}, 4, 2.0, clock=clock, sleep=clock.sleep))
    assert api.started == 4
    assert sorted(outcome.runs) == ["run_0", "run_2", "run_3"]
    assert outcome.start_errors == ["HTTP 503"]
    assert clock.sleeps == [0.5, 0.5, 0.5]


def test_poll_until_final_stops_polling_finished_runs():
    clock = FakeClock()
    api = FakeApi(scripts={"run_1": ["SUCCEEDED"]})
    outcome = Outcome(
        runs={f"run_{i}": {"run_id": f"run_{i}", "status": "QUEUED"} for i in range(2)}
    )
    rounds = []
    asyncio.run(
        poll_until_final(
            api,
            outcome,
            poll_s=5,
            on_round=lambda f, t: rounds.append((f, t)),
            clock=clock,
            sleep=clock.sleep,
        )
    )
    assert {r["status"] for r in outcome.runs.values()} == {"SUCCEEDED"}
    assert api.gets.count("run_1") == 1
    assert api.gets.count("run_0") == 2
    assert rounds == [(0, 2), (1, 2), (2, 2)]
    assert not outcome.timed_out


def test_poll_stops_at_waiting_approval_and_keeps_state_on_errors():
    clock = FakeClock()
    api = FakeApi(scripts={"run_0": ["error", "network", "WAITING_APPROVAL"]})
    outcome = Outcome(runs={"run_0": {"run_id": "run_0", "status": "QUEUED"}})
    asyncio.run(poll_until_final(api, outcome, poll_s=1, clock=clock, sleep=clock.sleep))
    assert outcome.runs["run_0"]["status"] == "WAITING_APPROVAL"


def test_poll_times_out():
    clock = FakeClock()
    api = FakeApi(scripts={"run_0": ["RUNNING"] * 100})
    outcome = Outcome(runs={"run_0": {"run_id": "run_0", "status": "QUEUED"}})
    asyncio.run(
        poll_until_final(api, outcome, poll_s=10, timeout_s=25, clock=clock, sleep=clock.sleep)
    )
    assert outcome.timed_out
    assert outcome.runs["run_0"]["status"] == "RUNNING"
    assert len(api.gets) == 3


def test_open_runs_counts_all_while_starting_then_those_not_final():
    assert open_runs(None, 1000) == 1000
    outcome = Outcome(
        runs={
            "a": {"status": "RUNNING"},
            "b": {"status": "SUCCEEDED"},
            "c": {"status": "QUEUED"},
            "d": {"status": "WAITING_APPROVAL"},
        }
    )
    assert open_runs(outcome, 1000) == 2
