"""Numbers from a finished test: percentiles, runs per minute, failures. Pure functions."""

from collections import Counter
from dataclasses import dataclass
from datetime import datetime

FINAL = frozenset({"SUCCEEDED", "FAILED", "CANCELLED"})


def percentile(values: list[float], p: float) -> float | None:
    """Linear interpolation between closest ranks (numpy's default); None for no values."""
    if not 0 <= p <= 100:
        raise ValueError(f"percentile must be 0 to 100, got {p}")
    if not values:
        return None
    ordered = sorted(values)
    rank = (len(ordered) - 1) * p / 100
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


def parse_time(text: str) -> datetime:
    """An API timestamp (ISO 8601, `Z` or an offset) as an aware datetime."""
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp without a time zone: {text}")
    return parsed


@dataclass(frozen=True)
class RunRecord:
    """What the driver knows about one run at the end of the test."""

    run_id: str
    trace_id: str
    status: str
    created_at: datetime
    updated_at: datetime
    failure_reason: str | None = None
    first_attempt_at: datetime | None = None
    attempts: int = 0

    @property
    def queue_to_start_s(self) -> float | None:
        if self.first_attempt_at is None:
            return None
        return (self.first_attempt_at - self.created_at).total_seconds()

    @property
    def total_s(self) -> float:
        return (self.updated_at - self.created_at).total_seconds()


@dataclass(frozen=True)
class Summary:
    runs: int
    succeeded: int
    not_final: int
    failures: dict[str, int]
    start_errors: int
    queue_to_start_p50_s: float | None
    queue_to_start_p95_s: float | None
    queue_to_start_max_s: float | None
    missing_first_attempt: int
    total_p50_s: float | None
    total_p95_s: float | None
    runs_per_minute: float | None
    wall_s: float | None
    working_runs_per_minute: float | None
    working_s: float | None
    redelivered: int


def summarize(records: list[RunRecord], start_errors: int = 0) -> Summary:
    """`failures` counts every run that did not succeed, by its failure reason or status.
    Runs per minute: final runs over the time from the first `created_at` to the last final
    `updated_at` (includes scale-out from 0). Working runs per minute: the same runs over the
    time from the first "run.attempt" start (workers up) to the last final `updated_at`."""
    final = [r for r in records if r.status in FINAL]
    succeeded = [r for r in records if r.status == "SUCCEEDED"]
    failures = Counter(r.failure_reason or r.status for r in records if r.status != "SUCCEEDED")
    waits = [r.queue_to_start_s for r in succeeded if r.queue_to_start_s is not None]
    totals = [r.total_s for r in succeeded]
    wall = None
    per_minute = None
    if final:
        wall = (
            max(r.updated_at for r in final) - min(r.created_at for r in records)
        ).total_seconds()
        per_minute = len(final) / (wall / 60) if wall > 0 else None
    working = None
    working_per_minute = None
    first_starts = [r.first_attempt_at for r in records if r.first_attempt_at is not None]
    if final and first_starts:
        working = (max(r.updated_at for r in final) - min(first_starts)).total_seconds()
        working_per_minute = len(final) / (working / 60) if working > 0 else None
    return Summary(
        runs=len(records),
        succeeded=len(succeeded),
        not_final=len(records) - len(final),
        failures=dict(sorted(failures.items())),
        start_errors=start_errors,
        queue_to_start_p50_s=percentile(waits, 50),
        queue_to_start_p95_s=percentile(waits, 95),
        queue_to_start_max_s=max(waits) if waits else None,
        missing_first_attempt=sum(1 for r in succeeded if r.first_attempt_at is None),
        total_p50_s=percentile(totals, 50),
        total_p95_s=percentile(totals, 95),
        runs_per_minute=per_minute,
        wall_s=wall,
        working_runs_per_minute=working_per_minute,
        working_s=working,
        redelivered=sum(1 for r in records if r.attempts > 1),
    )
