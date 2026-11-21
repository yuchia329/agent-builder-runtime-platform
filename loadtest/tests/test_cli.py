import json
from dataclasses import asdict
from datetime import UTC, datetime, timedelta

import pytest

from loadtest import aws, cli
from loadtest.chaos import Stop
from loadtest.driver import Outcome
from loadtest.stats import summarize

T0 = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


def test_parse_args_defaults_and_refusals():
    args = cli.parse_args([])
    assert (args.runs, args.rate, args.profile) == (100, 10.0, "default")
    with pytest.raises(SystemExit):
        cli.parse_args(["--profile", "prod"])
    with pytest.raises(SystemExit):
        cli.parse_args(["--runs", "0"])


def test_to_records_joins_spans_by_trace():
    outcome = Outcome(
        runs={
            "run_1": {
                "run_id": "run_1",
                "trace_id": "t1",
                "status": "SUCCEEDED",
                "created_at": "2026-11-20T12:00:00Z",
                "updated_at": "2026-11-20T12:00:40Z",
            },
            "run_2": {
                "run_id": "run_2",
                "trace_id": "t2",
                "status": "FAILED",
                "created_at": "2026-11-20T12:00:01Z",
                "updated_at": "2026-11-20T12:00:30Z",
                "failure": {"reason": "lease_lost", "message": "x"},
            },
        }
    )
    attempts = {"t1": aws.FirstAttempt(T0 + timedelta(seconds=12), 1, receives=2)}
    records = {r.run_id: r for r in cli.to_records(outcome, attempts)}
    assert records["run_1"].queue_to_start_s == 12.0
    assert records["run_1"].total_s == 40.0
    assert records["run_2"].failure_reason == "lease_lost"
    assert records["run_2"].first_attempt_at is None
    assert cli.record_json(records["run_1"])["queue_to_start_s"] == 12.0
    assert records["run_1"].receives == 2
    assert records["run_2"].receives == 0


def test_format_report_prints_the_numbers():
    outcome = Outcome(
        runs={
            f"run_{i}": {
                "run_id": f"run_{i}",
                "trace_id": f"t{i}",
                "status": "SUCCEEDED",
                "created_at": f"2026-11-20T12:00:0{i}Z",
                "updated_at": f"2026-11-20T12:00:3{i}Z",
            }
            for i in range(3)
        }
    )
    attempts = {f"t{i}": aws.FirstAttempt(T0 + timedelta(seconds=i + 5), 1) for i in range(3)}
    summary = summarize(cli.to_records(outcome, attempts))
    text = cli.format_report(
        summary, aws.WorkerMetrics(12.5, 40.0, 30.0, None), 4, {429: 2}, timed_out=False
    )
    assert "3/3 SUCCEEDED" in text
    assert "p50 5.0 s, p95 5.0 s" in text
    assert "runs per minute" in text
    assert "once working" in text
    assert "at most 4 running" in text
    assert "avg 12.5%, max 40.0%" in text
    assert "max -" in text
    assert "429 x2" in text


def test_save_results(tmp_path):
    path = cli.save_results(tmp_path / "results", {"settings": {"runs": 5}, "x": 1})
    assert path.name.endswith("-5.json")
    assert json.loads(path.read_text())["x"] == 1


def test_save_results_writes_times_and_metrics(tmp_path):
    metrics = aws.WorkerMetrics(1.0, 2.0, 3.0, 4.0, last_point=T0)
    path = cli.save_results(
        tmp_path, {"settings": {"runs": 1}, "worker_metrics": asdict(metrics), "at": T0}
    )
    saved = json.loads(path.read_text())
    assert saved["worker_metrics"]["last_point"] == "2026-11-20T12:00:00+00:00"
    assert saved["at"] == "2026-11-20T12:00:00+00:00"


def test_format_chaos():
    outcome = Outcome(
        runs={
            "run_1": {
                "run_id": "run_1",
                "trace_id": "t1",
                "status": "FAILED",
                "created_at": "2026-11-20T12:00:00Z",
                "updated_at": "2026-11-20T12:00:40Z",
            }
        }
    )
    summary = summarize(cli.to_records(outcome, {}))
    stop = Stop(at=T0, task_id="abc", open_runs=3)
    text = cli.format_chaos(
        summary,
        [stop, stop, stop],
        7,
        {"github.add_label": 1},
        chaos=True,
        exit_codes={"a": 137, "b": 0, "c": None},
    )
    assert "3 worker tasks stopped: 1 killed mid-run (exit 137), 1 finished" in text
    assert ", 1 other" in text
    assert "Lost runs         1" in text
    assert "7 ok" in text and "github.add_label x1" in text
    assert "Duplicate effects none (repeats of idempotent tools are allowed" in text
    slack = cli.format_chaos(summary, [], 3, {"slack.post_message": 1}, chaos=True)
    assert "Duplicate effects slack.post_message x1" in slack
    off = cli.format_chaos(summary, [], 0, {}, chaos=False)
    assert "Chaos             off" in off and "repeated: none" in off


def test_parse_args_chaos():
    assert cli.parse_args([]).chaos_every is None
    assert cli.parse_args(["--chaos-every", "20"]).chaos_every == 20.0
    with pytest.raises(SystemExit):
        cli.parse_args(["--chaos-every", "0"])
