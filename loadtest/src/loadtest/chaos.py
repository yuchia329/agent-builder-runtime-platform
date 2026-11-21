"""Chaos: stop random worker tasks while runs are in progress, and find repeated tool calls.

`ecs.stop_task` sends SIGTERM: the worker stops taking runs and finishes those in progress, and
ECS kills it (SIGKILL) after the stop timeout (Fargate's default, 30 s). Runs longer than that
(stub latency raised for the test) are cut mid-run; another worker takes them over from their
last checkpoint (ADR 0011-0013). The service starts a replacement task by itself.
"""

import asyncio
import json
import random
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

STOP_REASON = "loadtest chaos"
# Tools the gateway's fake connector marks idempotent (tool-gateway/src/gateway/connectors/fake.py).
# A worker killed between a call and storing its result leaves the outcome unknown; the next
# attempt repeats the call only for such tools (ADR 0013), which changes nothing (the same label
# twice is one label). A repeat of any other tool is a duplicate side effect.
SAFE_TO_REPEAT = frozenset({"github.search_issues", "github.add_label", "github.close_issue"})


@dataclass(frozen=True)
class Stop:
    at: datetime
    task_id: str
    open_runs: int


def running_tasks(ecs, cluster: str, service: str) -> list[str]:
    """ARNs of the service's tasks that are running now (not still starting or stopping)."""
    arns = ecs.list_tasks(cluster=cluster, serviceName=service, desiredStatus="RUNNING")["taskArns"]
    if not arns:
        return []
    described = ecs.describe_tasks(cluster=cluster, tasks=arns)["tasks"]
    return sorted(t["taskArn"] for t in described if t.get("lastStatus") == "RUNNING")


def stop_task(ecs, cluster: str, task_arn: str) -> None:
    ecs.stop_task(cluster=cluster, task=task_arn, reason=STOP_REASON)


async def chaos_loop(
    ecs,
    cluster: str,
    service: str,
    open_runs: Callable[[], int],
    *,
    every_s: float = 20.0,
    max_stops: int = 10,
    stops: list[Stop] | None = None,
    rng: random.Random | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> list[Stop]:
    """Every `every_s`, while runs are open, stops one random running task, up to `max_stops`.
    The first stop comes after one interval, so workers have taken runs by then. Ends by itself
    when no run is open; cancel it to end it sooner. Each stop is appended to `stops` as it
    happens, so a cancelled loop still leaves its record there."""
    rng = rng or random.Random()
    stops = [] if stops is None else stops
    while len(stops) < max_stops:
        await sleep(every_s)
        remaining = open_runs()
        if remaining == 0:
            break
        tasks = await asyncio.to_thread(running_tasks, ecs, cluster, service)
        if not tasks:
            continue
        task = rng.choice(tasks)
        await asyncio.to_thread(stop_task, ecs, cluster, task)
        stops.append(Stop(at=clock(), task_id=task.rsplit("/", 1)[-1], open_runs=remaining))
        print(f"   chaos: stopped task {stops[-1].task_id[:12]} ({remaining} runs open)")
    return stops


def stopped_exit_codes(ecs, cluster: str, task_ids: list[str]) -> dict[str, int | None]:
    """Exit code of each stopped task's container: 137 = killed by ECS after the stop timeout
    (runs were cut), 0 = it finished its runs and exited. None if not stopped yet (ECS keeps
    stopped tasks for about an hour)."""
    codes: dict[str, int | None] = {}
    for start in range(0, len(task_ids), 100):  # describe_tasks takes at most 100
        batch = task_ids[start : start + 100]
        for task in ecs.describe_tasks(cluster=cluster, tasks=batch)["tasks"]:
            task_id = task["taskArn"].rsplit("/", 1)[-1]
            containers = task.get("containers") or [{}]
            codes[task_id] = containers[0].get("exitCode")
    return codes


def parse_access_line(message: str) -> dict | None:
    """The JSON of a gateway access log line (`<time> INFO gateway.access {...}`), else None."""
    start = message.find("{")
    if start < 0 or "gateway.access" not in message[:start]:
        return None
    try:
        entry = json.loads(message[start:])
    except json.JSONDecodeError:
        return None
    return entry if entry.get("event") == "tool_call" else None


def repeated_calls(entries: list[dict], run_ids: set[str]) -> dict[str, int]:
    """Extra executions per tool: a call (its idempotency key; run, step, tool and arguments hash
    if it has none) that the gateway ran with outcome `ok` more than once, for the given runs."""
    counts: Counter[tuple] = Counter()
    tools: dict[tuple, str] = {}
    for entry in entries:
        if entry.get("run_id") not in run_ids or entry.get("outcome") != "ok":
            continue
        key = entry.get("idempotency_key") or (
            entry.get("run_id"),
            entry.get("step"),
            entry.get("tool"),
            entry.get("arguments_sha256"),
        )
        counts[key] += 1
        tools[key] = entry.get("tool", "?")
    extra: Counter[str] = Counter()
    for key, count in counts.items():
        if count > 1:
            extra[tools[key]] += count - 1
    return dict(sorted(extra.items()))


def duplicate_side_effects(repeated: dict[str, int]) -> dict[str, int]:
    """The repeats that are not allowed: tools not safe to repeat."""
    return {tool: n for tool, n in repeated.items() if tool not in SAFE_TO_REPEAT}
