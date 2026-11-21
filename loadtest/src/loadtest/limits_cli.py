"""Tenant-limits test (M8; ADR 0026, ADR 0027): three test tenants at once on the dev deployment.

- burst tenant (default `load-a`, cap 100 runs at once): 1,000 runs at 50 a second;
- trickle tenant (`load-b`): 1 run a second for 180 s, while the burst is under way;
- spend tenant (`load-c`, a tiny daily spend cap): one run at a time, each started when the
  last is final (spend grows only as runs finish), until it is refused; then 5 more starts, all
  of which must be refused; at most 60.

First a warm-up of the fleet: 300 burst-tenant runs, waited on until final and until at least 8
workers run, so the test starts with workers up (the M8 goal measures start times "workers up";
from 0 the service scales out one step at a time, and one warm-up run brings up one task). The
warm-up runs are not counted.

Load-test mode must be on (fake connector; the stub model answers 429 on 20% of calls and costs
$0.0001 a call). Keys come from ~/.config/agent-platform/<tenant>.key (0600), never printed or
saved. Prints per-tenant numbers and the M8 checks; exit 0 only if every check passes.

    cd loadtest
    uv run loadtest-limits
"""

import argparse
import asyncio
import contextlib
import json
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

import boto3
import httpx

from loadtest import aws
from loadtest.api import TENANT_BUDGET, Api, ApiError
from loadtest.driver import Outcome, poll_until_final, schedule
from loadtest.limits import StartAnswer, TenantNumbers, Verdict, tenant_numbers, verdict
from loadtest.spec import AGENT, EXAMPLE_SPEC, RUN_INPUT, load_spec
from loadtest.stats import parse_time

RESULTS_DIR = Path(__file__).resolve().parents[2] / "results"
SAMPLE_EVERY_S = 15


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--burst-tenant", default="load-a")
    parser.add_argument("--burst-cap", type=int, default=100, help="the burst tenant's max_running")
    parser.add_argument("--burst-runs", type=int, default=1000)
    parser.add_argument("--burst-rate", type=float, default=50.0, help="runs per second")
    parser.add_argument("--trickle-tenant", default="load-b")
    parser.add_argument("--trickle-runs", type=int, default=180)
    parser.add_argument("--trickle-rate", type=float, default=1.0)
    parser.add_argument("--spend-tenant", default="load-c")
    parser.add_argument("--spend-runs", type=int, default=60, help="most spend-tenant starts")
    parser.add_argument("--spend-rate", type=float, default=1.0)
    parser.add_argument(
        "--spend-after-refusal", type=int, default=5, help="starts sent after the first refusal"
    )
    parser.add_argument("--warm-runs", type=int, default=300, help="warm-up runs (burst tenant)")
    parser.add_argument("--warm-workers", type=int, default=8, help="workers to wait for")
    parser.add_argument("--warm-up-timeout", type=float, default=900.0)
    parser.add_argument("--start-p95", type=float, default=30.0, help="trickle p95 limit, s")
    parser.add_argument("--poll", type=float, default=10.0, help="seconds between status rounds")
    parser.add_argument("--timeout", type=float, default=2400.0, help="seconds to wait for runs")
    parser.add_argument("--span-wait", type=float, default=300.0)
    parser.add_argument("--key-dir", type=Path, default=aws.KEY_DIR)
    parser.add_argument("--profile", default="default")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--results-dir", type=Path, default=RESULTS_DIR)
    args = parser.parse_args(argv)
    if args.profile == "prod":
        parser.error("this project never uses the prod profile")
    tenants = [args.burst_tenant, args.trickle_tenant, args.spend_tenant]
    if len(set(tenants)) != 3:
        parser.error("the three tenants must differ")
    for count in (args.burst_runs, args.trickle_runs, args.spend_runs):
        if count < 1:
            parser.error("every tenant needs 1 run or more")
    if min(args.burst_rate, args.trickle_rate, args.spend_rate, args.poll) <= 0:
        parser.error("rates and --poll must be above 0")
    if args.burst_cap < 1:
        parser.error("--burst-cap must be 1 or more")
    if args.warm_runs < 0 or args.warm_workers < 0:
        parser.error("--warm-runs and --warm-workers must be 0 or more")
    if args.spend_after_refusal < 0:
        parser.error("--spend-after-refusal must be 0 or more")
    return args


async def start_ordered(
    api: Api,
    count: int,
    rate_per_s: float,
    *,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> tuple[list[StartAnswer], Outcome]:
    """Like driver.start_runs (each POST leaves on schedule), but keeps every answer in the order
    sent, so a refusal over the daily spend cap can be checked against later starts."""
    began = clock()
    pending = []
    for offset in schedule(count, rate_per_s):
        delay = began + offset - clock()
        if delay > 0:
            await sleep(delay)
        pending.append(asyncio.create_task(api.start_run(AGENT, RUN_INPUT)))
    answers: list[StartAnswer] = []
    outcome = Outcome()
    for index, result in enumerate(await asyncio.gather(*pending, return_exceptions=True)):
        if isinstance(result, ApiError) and result.reason == TENANT_BUDGET:
            answers.append(StartAnswer(index, None, refused=True))
        elif isinstance(result, BaseException):
            error = (
                f"HTTP {result.status}" if isinstance(result, ApiError) else type(result).__name__
            )
            answers.append(StartAnswer(index, None, error=error))
            outcome.start_errors.append(error)
        else:
            answers.append(StartAnswer(index, result["run_id"]))
            outcome.runs[result["run_id"]] = result
    return answers, outcome


async def start_until_refused(
    api: Api,
    max_runs: int,
    rate_per_s: float,
    after_refusal: int,
    *,
    until_final: Callable[[dict], Awaitable[dict]] | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> tuple[list[StartAnswer], Outcome]:
    """Starts runs one at a time, at most `rate_per_s`, until the first refusal over the daily
    spend cap, then `after_refusal` more (to show later starts are refused too), at most
    `max_runs`. With `until_final`, each created run is waited on (it returns the run's final
    state) before the next start, so the spend of every earlier run is counted."""
    began = clock()
    answers: list[StartAnswer] = []
    outcome = Outcome()
    left_after_refusal = None
    for index, offset in enumerate(schedule(max_runs, rate_per_s)):
        if left_after_refusal == 0:
            break
        delay = began + offset - clock()
        if delay > 0:
            await sleep(delay)
        try:
            run = await api.start_run(AGENT, RUN_INPUT)
        except ApiError as e:
            if e.reason == TENANT_BUDGET:
                answers.append(StartAnswer(index, None, refused=True))
            else:
                answers.append(StartAnswer(index, None, error=f"HTTP {e.status}"))
                outcome.start_errors.append(f"HTTP {e.status}")
        except httpx.HTTPError as e:
            answers.append(StartAnswer(index, None, error=type(e).__name__))
            outcome.start_errors.append(type(e).__name__)
        else:
            answers.append(StartAnswer(index, run["run_id"]))
            outcome.runs[run["run_id"]] = run
            if until_final is not None:
                outcome.runs[run["run_id"]] = await until_final(run)
                began = clock() - offset  # keep the spacing from here, not from the start
        if left_after_refusal is not None:
            left_after_refusal -= 1
        elif answers[-1].refused:
            left_after_refusal = after_refusal
    return answers, outcome


async def warm_up(
    api: Api,
    runs: int,
    workers: int,
    running: Callable[[], int],
    *,
    timeout_s: float,
    poll_s: float,
) -> float:
    """Starts `runs` runs at 50 a second, waits until they are final and `running()` (the latest
    task sample) reaches `workers`; returns the seconds it took."""
    began = time.monotonic()
    if runs:
        _, outcome = await start_ordered(api, runs, 50.0)
        await poll_until_final(api, outcome, poll_s=poll_s, timeout_s=timeout_s)
        bad = [r["status"] for r in outcome.runs.values() if r.get("status") != "SUCCEEDED"]
        if outcome.timed_out or bad or outcome.start_errors:
            raise RuntimeError(
                f"warm-up: {len(bad)} runs not SUCCEEDED, {len(outcome.start_errors)} not started"
            )
    while running() < workers:
        if time.monotonic() - began > timeout_s:
            raise RuntimeError(f"warm-up: {running()} workers running after {timeout_s:.0f} s")
        await asyncio.sleep(5)
    return time.monotonic() - began


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        target = aws.read_target()
        tenants = [args.burst_tenant, args.trickle_tenant, args.spend_tenant]
        keys = {t: aws.key_file(t, args.key_dir) for t in tenants}
    except aws.NotReady as e:
        sys.exit(f"Not ready: {e}")
    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    plan = [
        (args.burst_tenant, args.burst_runs, args.burst_rate),
        (args.trickle_tenant, args.trickle_runs, args.trickle_rate),
        (args.spend_tenant, args.spend_runs, args.spend_rate),
    ]
    for tenant, runs, rate in plan:
        print(f"{tenant}: {runs} runs at {rate:g}/s")
    began = datetime.now(UTC)
    results, samples, retries = asyncio.run(drive(args, target, keys, plan, session.client("ecs")))
    ended = datetime.now(UTC)

    all_runs = {rid: run for _, outcome in results.values() for rid, run in outcome.runs.items()}
    print("Reading run.attempt and model.call spans from aws/spans")
    spans = wait_for_attempts(session.client("logs"), all_runs, began, args.span_wait)
    print("Reading queue and worker metrics (published a minute or two late)")
    cloudwatch = session.client("cloudwatch")
    metrics = aws.wait_for_worker_metrics(cloudwatch, target.cluster, target.service, began, ended)
    queue_name = str(aws.terraform_output("run_queue_url")).rstrip("/").rsplit("/", 1)[-1]
    queue = aws.queue_metrics(cloudwatch, queue_name, began, ended)

    workers_up = min((a.started_at for a in spans), default=None)
    numbers = {}
    for tenant, (answers, outcome) in results.items():
        traces = {r["trace_id"] for r in outcome.runs.values()}
        numbers[tenant] = tenant_numbers(
            tenant,
            list(outcome.runs.values()),
            answers,
            [a for a in spans if a.trace_id in traces],
            workers_up,
            parse_time,
        )
    result = verdict(
        numbers[args.burst_tenant],
        numbers[args.trickle_tenant],
        numbers[args.spend_tenant],
        burst_cap=args.burst_cap,
        start_p95_limit_s=args.start_p95,
    )
    max_running = max((s.running for s in samples), default=0)
    print(format_report(numbers, queue, metrics, max_running, retries, workers_up, result))
    path = save_results(
        args.results_dir,
        {
            "settings": {
                k: v for k, v in vars(args).items() if not isinstance(v, Path)
            },  # no paths: the key directory names the home folder
            "began": began.isoformat(),
            "ended": ended.isoformat(),
            "workers_up": workers_up.isoformat() if workers_up else None,
            "tenants": {t: asdict(n) for t, n in numbers.items()},
            "checks": [{"check": c, "passed": ok} for c, ok in result.checks],
            "queue_metrics": asdict(queue),
            "worker_metrics": asdict(metrics),
            "max_running_tasks": max_running,
            "retries_by_status": retries,
            "task_samples": [
                {"at": s.at.isoformat(), "desired": s.desired, "running": s.running}
                for s in samples
            ],
            "runs": [
                {
                    "tenant": tenant,
                    "run_id": r["run_id"],
                    "trace_id": r["trace_id"],
                    "status": r["status"],
                    "failure_reason": (r.get("failure") or {}).get("reason"),
                    "cost_usd": r.get("cost_usd"),
                    "created_at": r["created_at"],
                    "updated_at": r["updated_at"],
                }
                for tenant, (_, outcome) in results.items()
                for r in outcome.runs.values()
            ],
            "attempts": [
                {
                    "trace_id": a.trace_id,
                    "started_at": a.started_at.isoformat(),
                    "ended_at": a.ended_at.isoformat(),
                    "status": a.status,
                    "receives": a.receives,
                    "model_calls": a.model_calls,
                }
                for a in spans
            ],
        },
    )
    print(f"Results: {path}")
    return 0 if result.passed else 1


async def drive(args, target: aws.Target, keys: dict[str, str], plan, ecs):
    samples: list[aws.TaskSample] = []
    stop = asyncio.Event()

    async def sample_loop() -> None:
        while not stop.is_set():
            try:
                samples.append(
                    await asyncio.to_thread(aws.sample_tasks, ecs, target.cluster, target.service)
                )
            except Exception as e:  # noqa: BLE001 - a missed sample does not stop the test
                print(f"   task sample failed: {type(e).__name__}")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), SAMPLE_EVERY_S)

    limits = httpx.Limits(max_connections=150, max_keepalive_connections=75)
    async with httpx.AsyncClient(base_url=target.api_url, timeout=35, limits=limits) as client:
        apis = {tenant: Api(client, keys[tenant]) for tenant, _, _ in plan}
        spec = load_spec(EXAMPLE_SPEC.read_text())
        for tenant, api in apis.items():
            made = await api.create_agent(AGENT)
            version = await api.publish(AGENT, spec)
            print(f"{tenant}: agent {AGENT} {'created' if made else 'exists'}, version {version}")
        sampler = asyncio.create_task(sample_loop())
        (burst, b_runs, b_rate), (trickle, t_runs, t_rate), (spend, s_runs, s_rate) = plan
        print(
            f"Warm-up: {args.warm_runs} {burst} runs, then at least {args.warm_workers} workers"
            " (not counted)"
        )
        took = await warm_up(
            apis[burst],
            args.warm_runs,
            args.warm_workers,
            lambda: samples[-1].running if samples else 0,
            timeout_s=args.warm_up_timeout,
            poll_s=args.poll,
        )
        running_now = samples[-1].running if samples else 0
        print(f"Warm-up done in {took:.0f} s, {running_now} workers running; starting the test")
        started = time.monotonic()

        async def spend_until_final(run: dict) -> dict:
            one = Outcome(runs={run["run_id"]: run})
            await poll_until_final(apis[spend], one, poll_s=2.0, timeout_s=args.timeout)
            return one.runs[run["run_id"]]

        starts = await asyncio.gather(
            start_ordered(apis[burst], b_runs, b_rate),
            start_ordered(apis[trickle], t_runs, t_rate),
            start_until_refused(
                apis[spend],
                s_runs,
                s_rate,
                args.spend_after_refusal,
                until_final=spend_until_final,
            ),
        )
        results = {tenant: start for (tenant, _, _), start in zip(plan, starts, strict=True)}
        for tenant, (answers, outcome) in results.items():
            refused = sum(1 for a in answers if a.refused)
            print(
                f"{tenant}: {len(outcome.runs)} runs started, {refused} refused (spend cap),"
                f" {len(outcome.start_errors)} failed to start"
            )

        last = {"line": ""}

        def progress(_final: int, _total: int) -> None:
            done = {
                t: sum(1 for r in o.runs.values() if r.get("status") in ("SUCCEEDED", "FAILED"))
                for t, (_, o) in results.items()
            }
            running = samples[-1].running if samples else 0
            line = (
                ", ".join(f"{t} {done[t]}/{len(o.runs)}" for t, (_, o) in results.items())
                + f"; {running} workers"
            )
            if line != last["line"]:
                print(f"   t+{time.monotonic() - started:4.0f} s {line}")
                last["line"] = line

        await asyncio.gather(
            *(
                poll_until_final(
                    apis[tenant],
                    outcome,
                    poll_s=args.poll,
                    timeout_s=args.timeout,
                    on_round=progress,
                )
                for tenant, (_, outcome) in results.items()
            )
        )
        stop.set()
        await sampler
        retries: dict[int, int] = {}
        for api in apis.values():
            for status, n in api.retries.by_status.items():
                retries[status] = retries.get(status, 0) + n
        return results, samples, retries


def wait_for_attempts(logs, runs: dict[str, dict], began: datetime, wait_s: float):
    """Attempt spans of the test's runs, asked again every 30 s until every succeeded run has an
    attempt that ended SUCCEEDED (spans arrive 20-30 s after an attempt ends) or `wait_s` passes."""
    traces = {r["trace_id"] for r in runs.values()}
    wanted = {r["trace_id"] for r in runs.values() if r.get("status") == "SUCCEEDED"}
    deadline = time.monotonic() + wait_s
    while True:
        found = aws.attempts(logs, traces, began - timedelta(minutes=1), datetime.now(UTC))
        done = {a.trace_id for a in found if a.status == "SUCCEEDED"}
        missing = wanted - done
        if not missing or time.monotonic() > deadline:
            if missing:
                print(f"   {len(missing)} succeeded runs have no final attempt span yet")
            return found
        time.sleep(30)


def format_report(
    numbers: dict[str, TenantNumbers],
    queue: aws.QueueMetrics,
    metrics: aws.WorkerMetrics,
    max_running: int,
    retries: dict[int, int],
    workers_up: datetime | None,
    result: Verdict,
) -> str:
    def secs(value: float | None) -> str:
        return "-" if value is None else f"{value:.1f} s"

    def num(value: float | None) -> str:
        return "-" if value is None else f"{value:.0f}"

    lines = [""]
    for n in numbers.values():
        failures = ", ".join(f"{k} {v}" for k, v in n.failures.items()) or "none"
        lines += [
            f"{n.tenant}",
            f"  Runs              {n.succeeded}/{n.runs} SUCCEEDED ({n.not_final} not final);"
            f" failures {failures}",
            f"  Starts            {n.refused} refused (spend cap), {n.start_errors} errors,"
            f" {n.created_after_first_refusal} created after the first refusal",
            f"  Queue to start    p50 {secs(n.queue_to_start_p50_s)}, p95"
            f" {secs(n.queue_to_start_p95_s)}, max {secs(n.queue_to_start_max_s)}; p95 once"
            f" workers up {secs(n.queue_to_start_p95_workers_up_s)} ({n.workers_up_runs} runs)",
            f"  Runs at once      {num(n.running_max_slots_inner)} to"
            f" {num(n.running_max_slots_outer)} (slot spans, {n.slots_measured} attempts);"
            f" attempt spans {n.running_max_upper}, model calls {n.running_max_lower}",
            f"  Put off at cap    {n.put_off_attempts} times ({n.runs_put_off} runs)",
            f"  Set aside (429)   {n.set_asides} times ({n.runs_set_aside} runs)",
            f"  Redelivered       {n.redelivered} runs; cost ${n.cost_usd:.4f}",
        ]
    lines += [
        f"Workers up        {workers_up.isoformat() if workers_up else '-'} (first attempt)",
        f"Queue             visible at most {num(queue.visible_max)}, of quiet tenants"
        f" {num(queue.visible_quiet_max)}",
        f"Workers           at most {max_running} running; CPU avg"
        f" {num(metrics.cpu_avg)}%, max {num(metrics.cpu_max)}%",
        "Retried answers   "
        + (", ".join(f"{k} x{v}" for k, v in sorted(retries.items())) or "none"),
        "",
        "Checks",
    ]
    lines += [f"  {'pass' if ok else 'FAIL'}  {check}" for check, ok in result.checks]
    lines.append("All checks passed" if result.passed else "Some checks FAILED")
    return "\n".join(lines)


def save_results(directory: Path, data: dict) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = directory / f"{stamp}-limits.json"
    path.write_text(json.dumps(data, indent=2, default=_json_value) + "\n")
    return path


def _json_value(value):
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"cannot write {type(value).__name__} to the results file")


if __name__ == "__main__":
    sys.exit(main())
