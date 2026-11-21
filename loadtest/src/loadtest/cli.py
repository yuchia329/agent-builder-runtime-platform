"""Load driver: publish `load-stub`, start N runs at a set rate, wait until each is final, then
print queue-to-start p50/p95, runs per minute, failures, and worker CPU and memory.

Needs load-test mode on (ADR 0024: `load_test = true` in infra/envs/dev/terraform.tfvars,
applied): the gateway on the fake connector, the stub model at a fixed latency, up to 20 workers.
Refuses otherwise. Reads the API address from `terraform output` and the tenant API key from SSM
(never printed or saved). Uses the AWS profile "default", never "prod".

    cd loadtest
    uv run loadtest --runs 100 --rate 10
    uv run loadtest --runs 100 --rate 10 --chaos-every 20   # stop a worker every 20 s

Results (every run, the summary, task samples; no secrets) go to results/<time>.json.
"""

import argparse
import asyncio
import contextlib
import json
import sys
import time
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

import boto3
import httpx

from loadtest import aws
from loadtest.api import Api
from loadtest.chaos import (
    Stop,
    chaos_loop,
    duplicate_side_effects,
    parse_access_line,
    repeated_calls,
    stopped_exit_codes,
)
from loadtest.driver import Outcome, open_runs, poll_until_final, start_runs
from loadtest.spec import AGENT, EXAMPLE_SPEC, RUN_INPUT, load_spec
from loadtest.stats import RunRecord, Summary, parse_time, summarize

RESULTS_DIR = Path(__file__).resolve().parents[2] / "results"
SAMPLE_EVERY_S = 15
MIN_LAMBDA_CONCURRENCY = 100


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--runs", type=int, default=100, help="runs to start (default 100)")
    parser.add_argument("--rate", type=float, default=10.0, help="runs started per second")
    parser.add_argument("--poll", type=float, default=10.0, help="seconds between status rounds")
    parser.add_argument("--timeout", type=float, default=1800.0, help="seconds to wait for runs")
    parser.add_argument(
        "--span-wait", type=float, default=300.0, help="seconds to wait for run.attempt spans"
    )
    parser.add_argument(
        "--chaos-every",
        type=float,
        default=None,
        help="stop a random worker task every S seconds while runs are open (off by default)",
    )
    parser.add_argument("--chaos-max", type=int, default=10, help="most tasks to stop")
    parser.add_argument("--profile", default="default")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--results-dir", type=Path, default=RESULTS_DIR)
    args = parser.parse_args(argv)
    if args.profile == "prod":
        parser.error("this project never uses the prod profile")
    if args.runs < 1 or args.rate <= 0 or args.poll <= 0:
        parser.error("--runs must be 1 or more; --rate and --poll above 0")
    if args.chaos_every is not None and (args.chaos_every <= 0 or args.chaos_max < 1):
        parser.error("--chaos-every must be above 0 and --chaos-max 1 or more")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        target = aws.read_target()
    except aws.NotReady as e:
        sys.exit(f"Not in load-test mode: {e}")
    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    try:
        key = aws.tenant_key(session.client("ssm"), target.key_parameter)
    except aws.NotReady as e:
        sys.exit(str(e))
    quota = aws.lambda_concurrency(session.client("service-quotas"))
    if quota is None or quota < MIN_LAMBDA_CONCURRENCY:
        print(f"Warning: Lambda concurrent executions limit is {quota}: expect 503s under load")

    print(f"{args.runs} runs of {AGENT} at {args.rate:g}/s against {target.api_url}")
    began = datetime.now(UTC)
    outcome, samples, retries, stops = asyncio.run(drive(args, target, key, session.client("ecs")))
    ended = datetime.now(UTC)

    print("Reading run.attempt spans from aws/spans")
    attempts = wait_for_spans(session.client("logs"), outcome, began, args.span_wait)
    print("Reading worker CPU and memory (CloudWatch publishes them a minute or two late)")
    metrics = aws.wait_for_worker_metrics(
        session.client("cloudwatch"), target.cluster, target.service, began, ended
    )
    print("Reading tool calls from the gateway's access log")
    entries = [
        e
        for m in aws.log_messages(
            session.client("logs"), target.gateway_log_group, '"tool_call"', began, ended
        )
        if (e := parse_access_line(m)) is not None
    ]
    run_ids = set(outcome.runs)
    calls_ok = sum(1 for e in entries if e.get("run_id") in run_ids and e.get("outcome") == "ok")
    repeated = repeated_calls(entries, run_ids)
    records = to_records(outcome, attempts)
    summary = summarize(records, start_errors=len(outcome.start_errors))
    max_running = max((s.running for s in samples), default=0)
    print(format_report(summary, metrics, max_running, retries, outcome.timed_out))
    exit_codes = (
        stopped_exit_codes(session.client("ecs"), target.cluster, [s.task_id for s in stops])
        if stops
        else {}
    )
    print(
        format_chaos(
            summary,
            stops,
            calls_ok,
            repeated,
            chaos=args.chaos_every is not None,
            exit_codes=exit_codes,
        )
    )

    path = save_results(
        args.results_dir,
        {
            "settings": {
                "runs": args.runs,
                "rate_per_s": args.rate,
                "poll_s": args.poll,
                "agent": AGENT,
                "lambda_concurrency": quota,
                "chaos_every_s": args.chaos_every,
                "chaos_max": args.chaos_max,
            },
            "began": began.isoformat(),
            "ended": ended.isoformat(),
            "summary": asdict(summary),
            "worker_metrics": asdict(metrics),
            "max_running_tasks": max_running,
            "retries_by_status": retries,
            "start_errors": outcome.start_errors,
            "timed_out": outcome.timed_out,
            "task_samples": [
                {"at": s.at.isoformat(), "desired": s.desired, "running": s.running}
                for s in samples
            ],
            "chaos_stops": [
                {
                    "at": s.at.isoformat(),
                    "task_id": s.task_id,
                    "open_runs": s.open_runs,
                    "exit_code": exit_codes.get(s.task_id),
                }
                for s in stops
            ],
            "tool_calls_ok": calls_ok,
            "repeated_calls_by_tool": repeated,
            "duplicate_side_effects_by_tool": duplicate_side_effects(repeated),
            "runs": [record_json(r) for r in records],
        },
    )
    print(f"Results: {path}")
    ok = (
        summary.succeeded == summary.runs
        and not outcome.start_errors
        and not outcome.timed_out
        and not duplicate_side_effects(repeated)
    )
    return 0 if ok else 1


async def drive(args, target: aws.Target, key: str, ecs):
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

    limits = httpx.Limits(max_connections=100, max_keepalive_connections=50)
    async with httpx.AsyncClient(base_url=target.api_url, timeout=35, limits=limits) as client:
        api = Api(client, key)
        made = await api.create_agent(AGENT)
        version = await api.publish(AGENT, load_spec(EXAMPLE_SPEC.read_text()))
        print(f"Agent {AGENT} {'created' if made else 'exists'}, version {version}")
        sampler = asyncio.create_task(sample_loop())
        started = time.monotonic()
        # Chaos starts with the runs: at 20 a second, 1,000 runs take 50 s to send.
        sent: dict[str, Outcome] = {}
        chaos = None
        stops: list[Stop] = []
        if args.chaos_every is not None:
            chaos = asyncio.create_task(
                chaos_loop(
                    ecs,
                    target.cluster,
                    target.service,
                    lambda: open_runs(sent.get("outcome"), args.runs),
                    every_s=args.chaos_every,
                    max_stops=args.chaos_max,
                    stops=stops,
                )
            )
        outcome = await start_runs(api, AGENT, RUN_INPUT, args.runs, args.rate)
        sent["outcome"] = outcome
        print(
            f"Started {len(outcome.runs)} runs in {time.monotonic() - started:.0f} s"
            f" ({len(outcome.start_errors)} failed to start)"
        )

        last = {"line": ""}

        def progress(final: int, total: int) -> None:
            running = samples[-1].running if samples else 0
            line = f"   {final}/{total} final, {running} workers running"
            if line != last["line"]:
                print(f"   t+{time.monotonic() - started:4.0f} s {line.strip()}")
                last["line"] = line

        await poll_until_final(
            api, outcome, poll_s=args.poll, timeout_s=args.timeout, on_round=progress
        )
        if chaos is not None:
            chaos.cancel()  # no effect if it already ended; stops stay in `stops`
            with contextlib.suppress(asyncio.CancelledError):
                await chaos
        stop.set()
        await sampler
        return outcome, samples, dict(api.retries.by_status), stops


def wait_for_spans(logs, outcome: Outcome, began: datetime, wait_s: float):
    """Queries until every finished run's first "run.attempt" span is in (spans arrive about
    20-30 s after the attempt ends) or `wait_s` passes."""
    wanted = {r["trace_id"] for r in outcome.runs.values() if r.get("status") == "SUCCEEDED"}
    deadline = time.monotonic() + wait_s
    while True:
        found = aws.first_attempts(
            logs,
            {r["trace_id"] for r in outcome.runs.values()},
            began - timedelta(minutes=1),
            datetime.now(UTC),
        )
        missing = wanted - found.keys()
        if not missing or time.monotonic() > deadline:
            if missing:
                print(f"   {len(missing)} runs have no run.attempt span after {wait_s:.0f} s")
            return found
        time.sleep(20)


def to_records(outcome: Outcome, attempts: dict[str, aws.FirstAttempt]) -> list[RunRecord]:
    records = []
    for run in outcome.runs.values():
        first = attempts.get(run["trace_id"])
        failure = run.get("failure") or {}
        records.append(
            RunRecord(
                run_id=run["run_id"],
                trace_id=run["trace_id"],
                status=run["status"],
                created_at=parse_time(run["created_at"]),
                updated_at=parse_time(run["updated_at"]),
                failure_reason=failure.get("reason"),
                first_attempt_at=first.started_at if first else None,
                attempts=first.attempts if first else 0,
                receives=first.receives if first else 0,
            )
        )
    return records


def record_json(record: RunRecord) -> dict:
    return {
        "run_id": record.run_id,
        "trace_id": record.trace_id,
        "status": record.status,
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
        "failure_reason": record.failure_reason,
        "first_attempt_at": record.first_attempt_at.isoformat()
        if record.first_attempt_at
        else None,
        "attempts": record.attempts,
        "receives": record.receives,
        "queue_to_start_s": record.queue_to_start_s,
    }


def format_report(
    summary: Summary,
    metrics: aws.WorkerMetrics,
    max_running: int,
    retries: dict[int, int],
    timed_out: bool,
) -> str:
    def secs(value: float | None) -> str:
        return "-" if value is None else f"{value:.1f} s"

    def pct(value: float | None) -> str:
        return "-" if value is None else f"{value:.1f}%"

    failures = ", ".join(f"{k} {v}" for k, v in summary.failures.items()) or "none"
    lines = [
        "",
        f"Runs              {summary.succeeded}/{summary.runs} SUCCEEDED"
        f" ({summary.not_final} not final{', timed out' if timed_out else ''})",
        f"Failures          {failures}; {summary.start_errors} failed to start",
        f"Queue to start    p50 {secs(summary.queue_to_start_p50_s)}, "
        f"p95 {secs(summary.queue_to_start_p95_s)}, max {secs(summary.queue_to_start_max_s)}"
        + (
            f" ({summary.missing_first_attempt} without a span)"
            if summary.missing_first_attempt
            else ""
        ),
        f"Created to final  p50 {secs(summary.total_p50_s)}, p95 {secs(summary.total_p95_s)}",
        "Throughput        "
        + (
            f"{summary.runs_per_minute:.1f} runs per minute over {summary.wall_s:.0f} s"
            if summary.runs_per_minute is not None
            else "-"
        ),
        "  once working    "
        + (
            f"{summary.working_runs_per_minute:.1f} runs per minute over {summary.working_s:.0f} s"
            " (first run.attempt to last final)"
            if summary.working_runs_per_minute is not None
            else "-"
        ),
        f"Redelivered runs  {summary.redelivered} (queue receive count above 1)",
        f"Workers           at most {max_running} running",
        f"Worker CPU        avg {pct(metrics.cpu_avg)}, max {pct(metrics.cpu_max)}",
        f"Worker memory     avg {pct(metrics.memory_avg)}, max {pct(metrics.memory_max)}",
        "Retried answers   "
        + (", ".join(f"{k} x{v}" for k, v in sorted(retries.items())) or "none"),
    ]
    return "\n".join(lines)


def format_chaos(
    summary: Summary,
    stops: list[Stop],
    calls_ok: int,
    repeated: dict[str, int],
    *,
    chaos: bool,
    exit_codes: dict[str, int | None] | None = None,
) -> str:
    lost = summary.runs - summary.succeeded
    codes = list((exit_codes or {}).values())
    killed = sum(1 for c in codes if c == 137)
    drained = sum(1 for c in codes if c == 0)
    other = len(stops) - killed - drained
    stopped = (
        f"Chaos             {len(stops)} worker tasks stopped: {killed} killed mid-run (exit 137), "
        f"{drained} finished their runs (exit 0)" + (f", {other} other" if other else "")
    )
    lines = [
        stopped if chaos else "Chaos             off",
        f"Lost runs         {lost} (not SUCCEEDED)",
        f"Tool calls        {calls_ok} ok in the gateway log, repeated: "
        + (", ".join(f"{tool} x{n}" for tool, n in repeated.items()) or "none"),
        "Duplicate effects "
        + (
            ", ".join(f"{tool} x{n}" for tool, n in duplicate_side_effects(repeated).items())
            or "none"
        )
        + (" (repeats of idempotent tools are allowed, ADR 0013)" if repeated else ""),
    ]
    return "\n".join(lines)


def save_results(directory: Path, data: dict) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = directory / f"{stamp}-{data['settings']['runs']}.json"
    path.write_text(json.dumps(data, indent=2, default=_json_value) + "\n")
    return path


def _json_value(value):
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"cannot write {type(value).__name__} to the results file")


if __name__ == "__main__":
    sys.exit(main())
