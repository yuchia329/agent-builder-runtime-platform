import asyncio
import contextlib
import json
import random
from datetime import UTC, datetime

from loadtest.chaos import (
    STOP_REASON,
    chaos_loop,
    duplicate_side_effects,
    parse_access_line,
    repeated_calls,
    running_tasks,
    stopped_exit_codes,
)

T0 = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
ARN = "arn:aws:ecs:us-east-1:000000000000:task/agent-platform-dev/"


class FakeEcs:
    def __init__(self, statuses):
        self.statuses = dict(statuses)  # task id -> lastStatus
        self.stopped: list[tuple[str, str]] = []

    def list_tasks(self, cluster, serviceName, desiredStatus):  # noqa: N803 - boto3 names
        assert (cluster, serviceName, desiredStatus) == ("c", "s", "RUNNING")
        return {"taskArns": [ARN + t for t in self.statuses]}

    def describe_tasks(self, cluster, tasks):
        return {
            "tasks": [
                {"taskArn": a, "lastStatus": self.statuses[a.rsplit("/", 1)[1]]} for a in tasks
            ]
        }

    def stop_task(self, cluster, task, reason):
        self.stopped.append((task.rsplit("/", 1)[1], reason))
        self.statuses.pop(task.rsplit("/", 1)[1])


async def no_sleep(seconds):
    await asyncio.sleep(0)


def test_running_tasks_skips_starting_ones():
    ecs = FakeEcs({"a": "RUNNING", "b": "PENDING", "c": "RUNNING"})
    assert [t.rsplit("/", 1)[1] for t in running_tasks(ecs, "c", "s")] == ["a", "c"]
    assert running_tasks(FakeEcs({}), "c", "s") == []


def test_chaos_stops_running_tasks_up_to_the_maximum():
    ecs = FakeEcs({"a": "RUNNING", "b": "RUNNING", "c": "RUNNING", "d": "PENDING"})
    stops = asyncio.run(
        chaos_loop(
            ecs,
            "c",
            "s",
            lambda: 5,
            max_stops=2,
            rng=random.Random(1),
            sleep=no_sleep,
            clock=lambda: T0,
        )
    )
    assert len(stops) == 2
    assert {s for s, _ in ecs.stopped} <= {"a", "b", "c"}
    assert all(reason == STOP_REASON for _, reason in ecs.stopped)
    assert stops[0].open_runs == 5
    assert stops[0].at == T0


def test_chaos_ends_when_no_run_is_open():
    ecs = FakeEcs({"a": "RUNNING"})
    left = iter([3, 0])
    stops = asyncio.run(chaos_loop(ecs, "c", "s", lambda: next(left), sleep=no_sleep))
    assert len(stops) == 1


def test_chaos_waits_when_no_task_is_running():
    ecs = FakeEcs({"a": "PENDING"})
    left = iter([3, 3, 0])
    stops = asyncio.run(chaos_loop(ecs, "c", "s", lambda: next(left), sleep=no_sleep))
    assert stops == []
    assert ecs.stopped == []


def test_cancelled_chaos_keeps_its_stops():
    ecs = FakeEcs({"a": "RUNNING", "b": "RUNNING"})
    stops = []

    async def main():
        calls = {"n": 0}

        async def sleep(seconds):
            calls["n"] += 1
            if calls["n"] > 1:
                await asyncio.sleep(3600)

        task = asyncio.create_task(chaos_loop(ecs, "c", "s", lambda: 2, stops=stops, sleep=sleep))
        for _ in range(20):
            await asyncio.sleep(0.01)
            if stops:
                break
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(main())
    assert len(stops) == 1


def line(**fields):
    entry = {"event": "tool_call", "outcome": "ok", "step": 1, "tool": "github.add_label"}
    entry.update(fields)
    return "2026-11-20 22:35:00,560 INFO gateway.access " + json.dumps(entry, sort_keys=True)


def test_parse_access_line():
    assert parse_access_line(line(run_id="r1"))["run_id"] == "r1"
    assert parse_access_line("2026-11-20 INFO gateway.app started") is None
    assert parse_access_line('2026-11-20 INFO other.logger {"event": "tool_call"}') is None
    assert parse_access_line("x INFO gateway.access {not json") is None
    assert parse_access_line('x INFO gateway.access {"event": "other"}') is None


def test_repeated_calls_counts_extra_ok_executions_per_key():
    entries = [
        parse_access_line(m)
        for m in [
            line(run_id="r1", idempotency_key="k1"),
            line(run_id="r1", idempotency_key="k1"),
            line(run_id="r1", idempotency_key="k1"),
            line(run_id="r1", idempotency_key="k2", tool="github.search_issues"),
            line(run_id="r2", idempotency_key="k3", outcome="error"),
            line(run_id="r2", idempotency_key="k3"),
            line(run_id="other", idempotency_key="k4"),
            line(run_id="other", idempotency_key="k4"),
        ]
    ]
    assert repeated_calls(entries, {"r1", "r2"}) == {"github.add_label": 2}


def test_repeated_calls_without_a_key_use_run_step_tool_and_arguments():
    entries = [
        parse_access_line(line(run_id="r1", arguments_sha256="h")),
        parse_access_line(line(run_id="r1", arguments_sha256="h")),
        parse_access_line(line(run_id="r1", arguments_sha256="other")),
    ]
    assert repeated_calls(entries, {"r1"}) == {"github.add_label": 1}
    assert repeated_calls([], {"r1"}) == {}


def test_stopped_exit_codes():
    class Ecs:
        def describe_tasks(self, cluster, tasks):
            return {
                "tasks": [
                    {"taskArn": ARN + "a", "containers": [{"exitCode": 137}]},
                    {"taskArn": ARN + "b", "containers": [{"exitCode": 0}]},
                    {"taskArn": ARN + "c", "containers": [{}]},
                ]
            }

    assert stopped_exit_codes(Ecs(), "c", ["a", "b", "c"]) == {"a": 137, "b": 0, "c": None}


def test_duplicate_side_effects_leave_out_idempotent_tools():
    repeated = {"github.add_label": 1, "slack.post_message": 2, "github.search_issues": 3}
    assert duplicate_side_effects(repeated) == {"slack.post_message": 2}
    assert duplicate_side_effects({}) == {}
