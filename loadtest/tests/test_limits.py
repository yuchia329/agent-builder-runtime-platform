import asyncio
import os
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from loadtest import aws
from loadtest.api import Api
from loadtest.limits import (
    StartAnswer,
    max_overlap,
    put_off,
    set_aside,
    tenant_numbers,
    verdict,
)
from loadtest.limits_cli import parse_args, start_ordered, start_until_refused
from loadtest.stats import parse_time

T0 = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def attempt(trace, start, end, status="SUCCEEDED", calls=3, receives=1, span=None):
    return aws.Attempt(
        trace_id=trace,
        span_id=span or f"{trace}-{start}",
        started_at=at(start),
        ended_at=at(end),
        status=status,
        receives=receives,
        model_calls=calls,
        first_call_at=at(start + 0.5) if calls else None,
        last_call_end=at(end - 0.5) if calls else None,
        slot=aws.SlotCalls(at(start + 0.05), at(start + 0.2), at(end - 0.3), at(end - 0.1))
        if calls
        else None,
    )


def run(trace, status="SUCCEEDED", created=0.0, updated=10.0, reason=None, cost=0.0003):
    return {
        "run_id": f"run_{trace}",
        "trace_id": trace,
        "status": status,
        "created_at": at(created).isoformat(),
        "updated_at": at(updated).isoformat(),
        "cost_usd": cost,
        **({"failure": {"reason": reason, "message": "x"}} if reason else {}),
    }


def test_max_overlap_counts_open_intervals_and_not_touching_ones():
    assert max_overlap([]) == (0, None)
    assert max_overlap([(at(0), at(10)), (at(5), at(15)), (at(12), at(20))]) == (2, at(5))
    assert max_overlap([(at(0), at(10)), (at(10), at(20))]) == (1, at(0))
    with pytest.raises(ValueError):
        max_overlap([(at(5), at(1))])


def test_attempt_kinds():
    assert put_off(attempt("t", 0, 0.2, status="QUEUED", calls=0))
    assert set_aside(attempt("t", 0, 5, status="QUEUED", calls=1))
    finished = attempt("t", 0, 5)
    assert not put_off(finished) and not set_aside(finished)


def test_tenant_numbers_from_runs_answers_and_spans():
    runs = [run("a"), run("b", created=1), run("c", created=2)]
    answers = [StartAnswer(0, "run_a"), StartAnswer(1, "run_b"), StartAnswer(2, "run_c")]
    spans = [
        attempt("a", 3, 13),
        attempt("b", 4, 4.1, status="QUEUED", calls=0),  # put off at the cap
        attempt("b", 35, 40, status="QUEUED", calls=1),  # set aside on a 429
        attempt("b", 70, 80, receives=2),
        attempt("c", 5, 12),
    ]

    n = tenant_numbers("load-a", runs, answers, spans, workers_up=at(3), parse_time=parse_time)

    assert (n.runs, n.succeeded, n.failures) == (3, 3, {})
    assert (n.put_off_attempts, n.runs_put_off) == (1, 1)
    assert (n.set_asides, n.runs_set_aside) == (1, 1)
    assert n.running_max_upper == 2  # a and c overlap; the put-off attempt holds no slot
    assert n.running_max_lower == 2
    assert n.queue_to_start_max_s == 3.0  # every run's first attempt, put-off ones included
    assert n.workers_up_runs == 0  # all three were created before the first worker was up
    assert n.redelivered == 1
    assert n.cost_usd == pytest.approx(0.0009)


def test_spend_tenant_refusals_and_starts_after_them():
    runs = [run("a"), run("b", status="FAILED", reason="tenant_budget")]
    answers = [
        StartAnswer(0, "run_a"),
        StartAnswer(1, "run_b"),
        StartAnswer(2, None, refused=True),
        StartAnswer(3, None, refused=True),
    ]

    n = tenant_numbers("load-c", runs, answers, [attempt("a", 1, 9)], None, parse_time)

    assert (n.refused, n.created_after_first_refusal) == (2, 0)
    assert n.failures == {"tenant_budget": 1}

    late = [*answers, StartAnswer(4, "run_late")]
    assert (
        tenant_numbers("load-c", runs, late, [], None, parse_time).created_after_first_refusal == 1
    )


def _numbers(tenant, runs, answers, spans, workers_up=T0):
    return tenant_numbers(tenant, runs, answers, spans, workers_up, parse_time)


def test_verdict_passes_only_when_every_m8_check_holds():
    burst = _numbers(
        "load-a",
        [run("a"), run("b")],
        [StartAnswer(0, "run_a"), StartAnswer(1, "run_b")],
        [attempt("a", 1, 10), attempt("b", 2, 11)],
    )
    trickle = _numbers(
        "load-b", [run("t", created=1)], [StartAnswer(0, "run_t")], [attempt("t", 3, 9)]
    )
    spend = _numbers(
        "load-c",
        [run("s")],
        [StartAnswer(0, "run_s"), StartAnswer(1, None, refused=True)],
        [attempt("s", 1, 5)],
    )

    assert verdict(burst, trickle, spend, burst_cap=2).passed
    over = verdict(burst, trickle, spend, burst_cap=1)
    assert not over.passed and not over.checks[0][1]

    slow = _numbers(
        "load-b", [run("t", created=1)], [StartAnswer(0, "run_t")], [attempt("t", 40, 45)]
    )
    assert not verdict(burst, slow, spend, burst_cap=2).passed

    never_refused = _numbers("load-c", [run("s")], [StartAnswer(0, "run_s")], [attempt("s", 1, 5)])
    assert not verdict(burst, trickle, never_refused, burst_cap=2).passed

    failed = _numbers(
        "load-a", [run("a", status="FAILED", reason="rate_limited")], [StartAnswer(0, "run_a")], []
    )
    assert not verdict(failed, trickle, spend, burst_cap=2).passed


def test_start_ordered_keeps_order_and_marks_refusals():
    answers = iter(
        [
            httpx.Response(201, json={"run_id": "run_1", "trace_id": "t1", "status": "QUEUED"}),
            httpx.Response(429, json={"status": 429, "reason": "tenant_budget"}),
            httpx.Response(400, json={"detail": "bad"}),
        ]
    )

    def handler(request):
        return next(answers)

    async def go():
        client = httpx.AsyncClient(
            base_url="https://api.test", transport=httpx.MockTransport(handler)
        )

        async def no_sleep(_):
            return None

        api = Api(client, "k", sleep=no_sleep)
        return await start_ordered(api, 3, 1000.0, sleep=no_sleep)

    found, outcome = asyncio.run(go())

    assert found == [
        StartAnswer(0, "run_1"),
        StartAnswer(1, None, refused=True),
        StartAnswer(2, None, error="HTTP 400"),
    ]
    assert list(outcome.runs) == ["run_1"] and outcome.start_errors == ["HTTP 400"]


def test_parse_args_defaults_and_refusals():
    args = parse_args([])
    assert (args.burst_tenant, args.burst_cap, args.burst_runs, args.burst_rate) == (
        "load-a",
        100,
        1000,
        50.0,
    )
    assert (args.trickle_runs, args.spend_runs, args.start_p95) == (180, 60, 30.0)
    assert (args.spend_after_refusal, args.warm_up_timeout) == (5, 900.0)
    for bad in (
        ["--profile", "prod"],
        ["--trickle-tenant", "load-a"],
        ["--spend-runs", "0"],
        ["--burst-rate", "0"],
        ["--burst-cap", "0"],
        ["--spend-after-refusal", "-1"],
    ):
        with pytest.raises(SystemExit):
            parse_args(bad)


def test_key_file_must_exist_be_private_and_not_empty(tmp_path):
    with pytest.raises(aws.NotReady, match="no key file"):
        aws.key_file("load-a", tmp_path)
    path = tmp_path / "load-a.key"
    path.write_text("ap_secret\n")
    os.chmod(path, 0o644)
    with pytest.raises(aws.NotReady, match="chmod 600"):
        aws.key_file("load-a", tmp_path)
    os.chmod(path, 0o600)
    assert aws.key_file("load-a", tmp_path) == "ap_secret"
    path.write_text("\n")
    with pytest.raises(aws.NotReady, match="empty"):
        aws.key_file("load-a", tmp_path)


def test_windows_cut_the_span_on_whole_seconds():
    pieces = aws.windows(at(0.4), at(130), 60)
    assert pieces == [(at(0), at(60)), (at(60), at(120)), (at(120), at(130))]


class FakeLogs:
    """Logs Insights: answers each query with the rows whose window holds them."""

    def __init__(self, calls, attempts, control=()):
        self.rows = {"model.call": calls, "run.attempt": attempts, "control": list(control)}
        self.queries = {}

    def start_query(self, logGroupName, startTime, endTime, queryString):  # noqa: N803
        if '"control_plane.update"' in queryString:
            kind = "control"
        elif '"model.call"' in queryString:
            kind = "model.call"
        else:
            kind = "run.attempt"
        rows = [r for when, r in self.rows[kind] if startTime <= when <= endTime]
        query_id = str(len(self.queries))
        self.queries[query_id] = rows
        return {"queryId": query_id}

    def get_query_results(self, queryId):  # noqa: N803
        rows = self.queries[queryId]
        return {
            "status": "Complete",
            "results": [[{"field": k, "value": v} for k, v in r.items()] for r in rows],
        }


def nanos(seconds):
    return str(int(at(seconds).timestamp() * 1e9))


def test_attempts_join_model_calls_to_their_attempt_across_window_edges():
    edge = int(at(60).timestamp())
    calls = [  # one attempt's calls split by the window edge, plus another trace's
        (
            edge,
            {
                "traceId": "t1",
                "parentSpanId": "s1",
                "calls": "2",
                "first_start": nanos(55),
                "last_end": nanos(60),
            },
        ),
        (
            edge,
            {
                "traceId": "t1",
                "parentSpanId": "s1",
                "calls": "1",
                "first_start": nanos(58),
                "last_end": nanos(61),
            },
        ),
        (
            edge,
            {
                "traceId": "other",
                "parentSpanId": "x",
                "calls": "1",
                "first_start": nanos(1),
                "last_end": nanos(2),
            },
        ),
    ]
    attempt_rows = [
        (
            edge,
            {
                "traceId": "t1",
                "spanId": "s1",
                "startTimeUnixNano": nanos(54),
                "endTimeUnixNano": nanos(62),
                "status": "SUCCEEDED",
                "receives": "1",
            },
        ),
        (
            int(at(10).timestamp()),
            {
                "traceId": "t1",
                "spanId": "s0",
                "startTimeUnixNano": nanos(9),
                "endTimeUnixNano": nanos(9.1),
                "status": "QUEUED",
            },
        ),
    ]

    found = aws.attempts(
        FakeLogs(calls, attempt_rows), {"t1"}, at(0), at(120), sleep=lambda _: None
    )

    by_span = {a.span_id: a for a in found}
    assert set(by_span) == {"s0", "s1"}  # s1 appears in two windows, kept once
    assert by_span["s1"].model_calls >= 3
    assert by_span["s1"].first_call_at == at(55) and by_span["s1"].last_call_end == at(61)
    assert by_span["s0"].model_calls == 0 and by_span["s0"].receives == 1


class FakeCloudWatch:
    def get_metric_data(self, MetricDataQueries, StartTime, EndTime):  # noqa: N803
        assert {q["MetricStat"]["Metric"]["MetricName"] for q in MetricDataQueries} == {
            "ApproximateNumberOfMessagesVisible",
            "ApproximateNumberOfMessagesVisibleInQuietGroups",
        }
        return {
            "MetricDataResults": [
                {"Id": "visible", "Values": [10.0, 640.0, 300.0]},
                {"Id": "quiet", "Values": [0.0, 3.0]},
            ]
        }


def test_queue_metrics_take_the_highest_points():
    assert aws.queue_metrics(FakeCloudWatch(), "q", at(0), at(60)) == aws.QueueMetrics(640.0, 3.0)


def cp_call(name, start, end, requested=None, outcome="ok", parent="s1", trace="t1"):
    row = {
        "traceId": trace,
        "parentSpanId": parent,
        "name": name,
        "startTimeUnixNano": nanos(start),
        "endTimeUnixNano": nanos(end),
        "outcome": outcome,
    }
    if requested:
        row["requested"] = requested
    return row


def test_slot_times_from_the_start_patch_and_the_call_that_frees_the_slot():
    finished = [
        cp_call("control_plane.update", 1, 4.5, "RUNNING"),  # waited on a busy counter
        cp_call("control_plane.update", 20, 20.2, "SUCCEEDED"),
    ]
    assert aws.slot_times(finished) == aws.SlotCalls(at(1), at(4.5), at(20), at(20.2))

    set_aside = [
        cp_call("control_plane.update", 1, 1.1, "RUNNING"),
        cp_call("control_plane.update", 9, 9.1, "RUNNING"),  # progress before the defer
        cp_call("control_plane.defer", 9.1, 9.3),
    ]
    assert aws.slot_times(set_aside) == aws.SlotCalls(at(1), at(1.1), at(9.1), at(9.3))

    put_off = [cp_call("control_plane.update", 1, 1.2, "RUNNING", outcome="deferred")]
    assert aws.slot_times(put_off) is None
    assert aws.slot_times([]) is None
    assert aws.slot_times(finished[:1]) is None  # never freed in this attempt


def test_attempts_carry_slot_times():
    edge = int(at(30).timestamp())
    attempt_rows = [
        (
            edge,
            {
                "traceId": "t1",
                "spanId": "s1",
                "startTimeUnixNano": nanos(0),
                "endTimeUnixNano": nanos(21),
                "status": "SUCCEEDED",
            },
        ),
    ]
    calls = [
        (
            edge,
            {
                "traceId": "t1",
                "parentSpanId": "s1",
                "calls": "3",
                "first_start": nanos(5),
                "last_end": nanos(19),
            },
        )
    ]
    control = [
        (edge, cp_call("control_plane.update", 0.1, 4.5, "RUNNING")),
        (edge, cp_call("control_plane.update", 20, 20.2, "SUCCEEDED")),
    ]

    (found,) = aws.attempts(
        FakeLogs(calls, attempt_rows, control), {"t1"}, at(0), at(60), sleep=lambda _: None
    )

    assert found.slot == aws.SlotCalls(at(0.1), at(4.5), at(20), at(20.2))
    assert found.slot.inner == (at(4.5), at(20)) and found.slot.outer == (at(0.1), at(20.2))


def test_slot_counts_bracket_the_true_number_and_need_every_attempt_measured():
    # b's start PATCH is still waiting while a holds its slot; they overlap only in outer time.
    a = aws.Attempt(
        "a",
        "sa",
        at(0),
        at(20),
        "SUCCEEDED",
        1,
        3,
        at(6),
        at(19),
        aws.SlotCalls(at(0.1), at(5.5), at(19.5), at(20)),
    )
    b = aws.Attempt(
        "b",
        "sb",
        at(1),
        at(30),
        "SUCCEEDED",
        1,
        3,
        at(21),
        at(29),
        aws.SlotCalls(at(1.1), at(20.5), at(29.5), at(30)),
    )
    n = tenant_numbers("load-a", [run("a"), run("b")], [], [a, b], T0, parse_time)
    assert (n.running_max_slots_inner, n.running_max_slots_outer, n.slots_measured) == (1, 2, 2)
    assert n.running_max_upper == 2

    unmeasured = aws.Attempt("b", "sb", at(1), at(5), "SUCCEEDED", 1, 1, at(2), at(4))
    n = tenant_numbers("load-a", [run("a"), run("b")], [], [a, unmeasured], T0, parse_time)
    assert n.running_max_slots_inner is None and n.slots_measured == 1


def test_start_until_refused_stops_a_few_starts_after_the_first_refusal():
    answers = iter(
        [httpx.Response(201, json={"run_id": f"run_{i}", "trace_id": f"t{i}"}) for i in range(3)]
        + [httpx.Response(429, json={"reason": "tenant_budget"})] * 10
    )
    sent = []

    def handler(request):
        sent.append(1)
        return next(answers)

    async def go():
        client = httpx.AsyncClient(
            base_url="https://api.test", transport=httpx.MockTransport(handler)
        )

        async def no_sleep(_):
            return None

        api = Api(client, "k", sleep=no_sleep)
        return await start_until_refused(api, 60, 1000.0, 2, sleep=no_sleep)

    found, outcome = asyncio.run(go())

    assert [a.refused for a in found] == [False, False, False, True, True, True]
    assert len(sent) == 6 and list(outcome.runs) == ["run_0", "run_1", "run_2"]


def test_start_until_refused_waits_for_each_run_when_asked():
    answers = iter(
        [httpx.Response(201, json={"run_id": f"run_{i}", "trace_id": f"t{i}"}) for i in range(2)]
        + [httpx.Response(429, json={"reason": "tenant_budget"})] * 5
    )
    waited = []

    def handler(request):
        return next(answers)

    async def go():
        client = httpx.AsyncClient(
            base_url="https://api.test", transport=httpx.MockTransport(handler)
        )

        async def no_sleep(_):
            return None

        async def until_final(run):
            waited.append(run["run_id"])
            return run | {"status": "SUCCEEDED"}

        api = Api(client, "k", sleep=no_sleep)
        return await start_until_refused(
            api, 60, 1000.0, 1, until_final=until_final, sleep=no_sleep
        )

    found, outcome = asyncio.run(go())

    assert waited == ["run_0", "run_1"]
    assert [a.refused for a in found] == [False, False, True, True]
    assert {r["status"] for r in outcome.runs.values()} == {"SUCCEEDED"}
