"""Numbers for the tenant-limits test (M8; ADR 0026, ADR 0027). Pure functions over what the
driver collected: each tenant's runs (API), start answers, and the workers' attempt spans.

Runs executing at once, per tenant, come from spans, not the control-plane table (only the
control plane reads it). A run holds a slot from its start (PATCH to RUNNING) to its end or until
it is set aside; that happens inside a worker attempt that called the model. So:

- slots, from the worker's control-plane call spans. The slot is taken inside the start PATCH
  and freed inside the final PATCH or defer; the client cannot see the instant of the write, so
  two counts bracket it. Inner (start answered to freeing call sent) lies inside every slot: it
  is at most the true number, so above the cap means the cap was broken (the check). Outer
  (start sent to freeing call answered) contains every slot: at least the true number;
- upper bound: attempt span start to end. It also contains the time a start PATCH waits on a
  busy slot counter (seconds under a burst), so it overcounts;
- lower bound: each attempt's first model call start to last model call end.

An attempt that called no model and ended QUEUED is a start put off at the cap. One that called
the model and ended QUEUED is a run set aside (the model kept answering 429).
"""

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime

from loadtest.aws import Attempt
from loadtest.stats import FINAL, RunRecord, percentile


def max_overlap(intervals: Iterable[tuple[datetime, datetime]]) -> tuple[int, datetime | None]:
    """The most intervals open at one time and when that first happened. Touching intervals (one
    ends when the next starts) do not overlap."""
    events = []
    for start, end in intervals:
        if end < start:
            raise ValueError(f"interval ends before it starts: {start} {end}")
        events.append((start, 1))
        events.append((end, -1))
    events.sort(key=lambda e: (e[0], e[1]))  # ends before starts at the same instant
    best, at, open_now = 0, None, 0
    for when, change in events:
        open_now += change
        if open_now > best:
            best, at = open_now, when
    return best, at


def held_slot(attempt: Attempt) -> bool:
    return attempt.model_calls > 0


def put_off(attempt: Attempt) -> bool:
    return attempt.model_calls == 0 and attempt.status == "QUEUED"


def set_aside(attempt: Attempt) -> bool:
    return attempt.model_calls > 0 and attempt.status == "QUEUED"


@dataclass(frozen=True)
class StartAnswer:
    """One POST /runs of a tenant, in the order sent."""

    index: int
    run_id: str | None  # None: no run was created
    refused: bool = False  # 429 tenant_budget (over the daily spend cap)
    error: str | None = None  # anything else that kept the run from being created


@dataclass
class TenantNumbers:
    tenant: str
    runs: int
    succeeded: int
    failures: dict[str, int]
    not_final: int
    start_errors: int
    refused: int
    created_after_first_refusal: int
    queue_to_start_p50_s: float | None
    queue_to_start_p95_s: float | None
    queue_to_start_max_s: float | None
    queue_to_start_p95_workers_up_s: float | None  # runs created once a worker was up
    workers_up_runs: int
    missing_first_attempt: int
    running_max_upper: int
    running_max_upper_at: datetime | None
    running_max_lower: int
    # None: some attempt that held a slot has no control-plane call spans
    running_max_slots_inner: int | None
    running_max_slots_outer: int | None
    slots_measured: int  # attempts with both control-plane calls
    put_off_attempts: int
    runs_put_off: int
    set_asides: int
    runs_set_aside: int
    redelivered: int  # runs whose message was received more than once (an error kept it)
    cost_usd: float
    extra: dict = field(default_factory=dict)


def first_attempt_starts(attempts: Iterable[Attempt]) -> dict[str, datetime]:
    first: dict[str, datetime] = {}
    for a in attempts:
        if a.trace_id not in first or a.started_at < first[a.trace_id]:
            first[a.trace_id] = a.started_at
    return first


def tenant_numbers(
    tenant: str,
    runs: list[dict],
    answers: list[StartAnswer],
    attempts: list[Attempt],
    workers_up: datetime | None,
    parse_time,
) -> TenantNumbers:
    """`runs`: the tenant's runs as GET /runs/{id} last answered; `attempts`: the attempt spans
    of those runs' traces; `workers_up`: when the first attempt of the whole test started."""
    first = first_attempt_starts(attempts)
    records = []
    for run in runs:
        failure = run.get("failure") or {}
        records.append(
            RunRecord(
                run_id=run["run_id"],
                trace_id=run["trace_id"],
                status=run["status"],
                created_at=parse_time(run["created_at"]),
                updated_at=parse_time(run["updated_at"]),
                failure_reason=failure.get("reason"),
                first_attempt_at=first.get(run["trace_id"]),
            )
        )
    succeeded = [r for r in records if r.status == "SUCCEEDED"]
    waits = [r.queue_to_start_s for r in succeeded if r.queue_to_start_s is not None]
    up_waits = [
        r.queue_to_start_s
        for r in succeeded
        if r.queue_to_start_s is not None and workers_up is not None and r.created_at >= workers_up
    ]
    holding = [a for a in attempts if held_slot(a)]
    upper, upper_at = max_overlap((a.started_at, a.ended_at) for a in holding)
    lower, _ = max_overlap(
        (a.first_call_at, a.last_call_end)
        for a in holding
        if a.first_call_at is not None and a.last_call_end is not None
    )
    measured = [a.slot for a in holding if a.slot is not None]
    complete = bool(holding) and len(measured) == len(holding)
    inner, _ = max_overlap(c.inner for c in measured)
    outer, _ = max_overlap(c.outer for c in measured)
    refusals = [a.index for a in answers if a.refused]
    first_refusal = min(refusals, default=None)
    receives: Counter[str] = Counter()
    for a in attempts:
        receives[a.trace_id] = max(receives[a.trace_id], a.receives)
    return TenantNumbers(
        tenant=tenant,
        runs=len(records),
        succeeded=len(succeeded),
        failures=dict(
            sorted(
                Counter(
                    r.failure_reason or r.status for r in records if r.status != "SUCCEEDED"
                ).items()
            )
        ),
        not_final=sum(1 for r in records if r.status not in FINAL),
        start_errors=sum(1 for a in answers if a.error is not None),
        refused=len(refusals),
        created_after_first_refusal=0
        if first_refusal is None
        else sum(1 for a in answers if a.index > first_refusal and a.run_id is not None),
        queue_to_start_p50_s=percentile(waits, 50),
        queue_to_start_p95_s=percentile(waits, 95),
        queue_to_start_max_s=max(waits) if waits else None,
        queue_to_start_p95_workers_up_s=percentile(up_waits, 95),
        workers_up_runs=len(up_waits),
        missing_first_attempt=sum(1 for r in succeeded if r.first_attempt_at is None),
        running_max_upper=upper,
        running_max_upper_at=upper_at,
        running_max_lower=lower,
        running_max_slots_inner=inner if complete else None,
        running_max_slots_outer=outer if complete else None,
        slots_measured=len(measured),
        put_off_attempts=sum(1 for a in attempts if put_off(a)),
        runs_put_off=len({a.trace_id for a in attempts if put_off(a)}),
        set_asides=sum(1 for a in attempts if set_aside(a)),
        runs_set_aside=len({a.trace_id for a in attempts if set_aside(a)}),
        redelivered=sum(1 for n in receives.values() if n > 1),
        cost_usd=round(sum(float(r.get("cost_usd") or 0) for r in runs), 6),
    )


@dataclass(frozen=True)
class Verdict:
    passed: bool
    checks: list[tuple[str, bool]]


def verdict(
    burst: TenantNumbers,
    trickle: TenantNumbers,
    spend: TenantNumbers,
    *,
    burst_cap: int,
    start_p95_limit_s: float = 30.0,
) -> Verdict:
    """The M8 row: the burst tenant never above its cap, no run of the burst or trickle tenant
    fails (with the stub's 429s on), the trickle tenant starts within 30 s at p95 once workers are
    up, and the spend tenant gets new runs refused (and nothing created after the first refusal,
    and no failure other than tenant_budget)."""

    def clean(t: TenantNumbers) -> bool:
        return t.succeeded == t.runs and t.start_errors == 0 and t.refused == 0 and t.runs > 0

    p95 = trickle.queue_to_start_p95_workers_up_s
    slots = burst.running_max_slots_inner
    checks = [
        (
            f"{burst.tenant} at most {burst_cap} runs at once (slot spans, inner count)",
            slots is not None and slots <= burst_cap,
        ),
        (f"{burst.tenant}: every run SUCCEEDED", clean(burst)),
        (f"{trickle.tenant}: every run SUCCEEDED", clean(trickle)),
        (
            f"{trickle.tenant}: queue to start p95 within {start_p95_limit_s:g} s (workers up)",
            p95 is not None and p95 <= start_p95_limit_s,
        ),
        (f"{spend.tenant}: new runs refused over the daily cap", spend.refused > 0),
        (
            f"{spend.tenant}: no run created after the first refusal",
            spend.created_after_first_refusal == 0,
        ),
        (
            f"{spend.tenant}: failures are tenant_budget only",
            set(spend.failures) <= {"tenant_budget"} and spend.start_errors == 0,
        ),
    ]
    return Verdict(all(ok for _, ok in checks), checks)
