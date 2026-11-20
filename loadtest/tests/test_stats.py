from datetime import UTC, datetime, timedelta

import pytest

from loadtest.stats import RunRecord, parse_time, percentile, summarize

T0 = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


def record(i: int, status="SUCCEEDED", wait=None, total=10.0, reason=None, attempts=1):
    created = T0 + timedelta(seconds=i)
    return RunRecord(
        run_id=f"run_{i}",
        trace_id=f"{i:032x}",
        status=status,
        created_at=created,
        updated_at=created + timedelta(seconds=total),
        failure_reason=reason,
        first_attempt_at=None if wait is None else created + timedelta(seconds=wait),
        attempts=attempts if wait is not None else 0,
    )


def test_percentile_interpolates_between_ranks():
    values = [1.0, 2.0, 3.0, 4.0]
    assert percentile(values, 0) == 1.0
    assert percentile(values, 50) == 2.5
    assert percentile(values, 100) == 4.0
    assert percentile(values, 95) == pytest.approx(3.85)


def test_percentile_of_one_value_and_none():
    assert percentile([7.0], 95) == 7.0
    assert percentile([], 50) is None


def test_percentile_order_does_not_matter():
    assert percentile([5.0, 1.0, 3.0], 50) == 3.0


def test_percentile_out_of_range():
    with pytest.raises(ValueError):
        percentile([1.0], 101)


def test_parse_time_accepts_z_and_offsets():
    assert parse_time("2026-11-20T12:00:00.123456Z") == T0 + timedelta(microseconds=123456)
    assert parse_time("2026-11-20T13:00:00+01:00") == T0


def test_parse_time_refuses_naive():
    with pytest.raises(ValueError):
        parse_time("2026-11-20T12:00:00")


def test_summarize_all_succeeded():
    records = [record(i, wait=float(i + 1), total=20.0) for i in range(10)]
    summary = summarize(records)
    assert summary.runs == 10
    assert summary.succeeded == 10
    assert summary.failures == {}
    assert summary.not_final == 0
    assert summary.queue_to_start_p50_s == 5.5
    assert summary.queue_to_start_max_s == 10.0
    assert summary.total_p95_s == 20.0
    # first created at 0 s, last final at 9 + 20 = 29 s: 10 runs in 29 s
    assert summary.wall_s == 29.0
    assert summary.runs_per_minute == pytest.approx(10 / (29 / 60))
    # first attempt at 0 + 1 = 1 s, last final at 29 s
    assert summary.working_s == 28.0
    assert summary.working_runs_per_minute == pytest.approx(10 / (28 / 60))
    assert summary.redelivered == 0


def test_summarize_counts_failures_by_reason_and_status():
    records = [
        record(0, wait=1.0),
        record(1, status="FAILED", wait=1.0, reason="budget_exceeded"),
        record(2, status="FAILED", wait=1.0, reason="budget_exceeded"),
        record(3, status="RUNNING", wait=1.0),
        record(4, status="WAITING_APPROVAL", wait=1.0),
    ]
    summary = summarize(records, start_errors=2)
    assert summary.succeeded == 1
    assert summary.failures == {"RUNNING": 1, "WAITING_APPROVAL": 1, "budget_exceeded": 2}
    assert summary.not_final == 2
    assert summary.start_errors == 2


def test_summarize_waits_only_from_succeeded_runs_with_a_span():
    records = [record(0, wait=2.0), record(1, wait=None), record(2, status="FAILED", wait=50.0)]
    summary = summarize(records)
    assert summary.queue_to_start_max_s == 2.0
    assert summary.missing_first_attempt == 1


def test_summarize_counts_redelivered_runs():
    summary = summarize([record(0, wait=1.0, attempts=2), record(1, wait=1.0)])
    assert summary.redelivered == 1


def test_summarize_nothing_final():
    summary = summarize([record(0, status="QUEUED")])
    assert summary.runs_per_minute is None
    assert summary.wall_s is None
    assert summary.working_runs_per_minute is None
    assert summary.queue_to_start_p50_s is None
