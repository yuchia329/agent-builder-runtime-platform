from datetime import UTC, datetime, timedelta

import pytest

from loadtest import aws

T0 = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)

LOAD_OUTPUTS = {
    "load_test": True,
    "gateway_connectors": "fake",
    "api_url": "https://api.test/",
    "secret_parameters": {"TENANT_API_KEY": "/agent-platform/dev/control-plane/tenant-api-key"},
    "worker_service": {"cluster": "c", "service": "s"},
    "functions": {"control_plane": "cp", "gateway": "agent-platform-dev-tool-gateway"},
}


def test_read_target_in_load_mode():
    target = aws.read_target(LOAD_OUTPUTS.__getitem__)
    assert target == aws.Target(
        api_url="https://api.test",
        key_parameter="/agent-platform/dev/control-plane/tenant-api-key",
        cluster="c",
        service="s",
        gateway_log_group="/aws/lambda/agent-platform-dev-tool-gateway",
    )


@pytest.mark.parametrize(
    "change",
    [{"load_test": False}, {"gateway_connectors": "github"}, {"gateway_connectors": "fake,github"}],
)
def test_read_target_refuses_outside_load_mode(change):
    outputs = LOAD_OUTPUTS | change
    with pytest.raises(aws.NotReady):
        aws.read_target(outputs.__getitem__)


class FakeSsm:
    def __init__(self, version):
        self.version = version

    def get_parameter(self, Name, WithDecryption):  # noqa: N803 - boto3 names
        assert WithDecryption
        return {"Parameter": {"Version": self.version, "Value": "k"}}


def test_tenant_key_refuses_placeholder():
    assert aws.tenant_key(FakeSsm(2), "/p") == "k"
    with pytest.raises(aws.NotReady):
        aws.tenant_key(FakeSsm(1), "/p")


class FakeQuotas:
    def __init__(self, value=None, error=False):
        self.value, self.error = value, error

    def get_service_quota(self, ServiceCode, QuotaCode):  # noqa: N803
        if self.error:
            raise RuntimeError("denied")
        assert (ServiceCode, QuotaCode) == ("lambda", "L-B99A9384")
        return {"Quota": {"Value": self.value}}


def test_lambda_concurrency():
    assert aws.lambda_concurrency(FakeQuotas(1000.0)) == 1000.0
    assert aws.lambda_concurrency(FakeQuotas(error=True)) is None


class FakeLogs:
    def __init__(self, statuses, rows):
        self.statuses = list(statuses)
        self.rows = rows
        self.started = None

    def start_query(self, **kwargs):
        self.started = kwargs
        return {"queryId": "q1"}

    def get_query_results(self, queryId):  # noqa: N803
        status = self.statuses.pop(0)
        return {"status": status, "results": self.rows if status == "Complete" else []}


def row(trace_id, nanos, attempts, receives=None):
    fields = [
        {"field": "traceId", "value": trace_id},
        {"field": "first_start", "value": nanos},
        {"field": "attempts", "value": attempts},
    ]
    if receives is not None:
        fields.append({"field": "receives", "value": receives})
    return fields


def test_first_attempts_keeps_only_wanted_traces():
    started_ns = int((T0 + timedelta(seconds=30)).timestamp() * 1e9)
    logs = FakeLogs(
        ["Running", "Complete"],
        [
            row("a" * 32, str(started_ns), "1"),
            row("b" * 32, "1.7635e18", "2", "3"),
            row("other", "1", "1"),
        ],
    )
    sleeps = []
    found = aws.first_attempts(
        logs, {"a" * 32, "b" * 32}, T0, T0 + timedelta(minutes=5), sleep=sleeps.append
    )
    assert set(found) == {"a" * 32, "b" * 32}
    assert found["a" * 32] == aws.FirstAttempt(started_at=T0 + timedelta(seconds=30), attempts=1)
    assert found["b" * 32].attempts == 2
    assert found["b" * 32].receives == 3
    assert found["a" * 32].receives == 1  # no receive count: first delivery
    assert "queue.receive_count" in logs.started["queryString"]
    assert logs.started["logGroupName"] == "aws/spans"
    assert logs.started["startTime"] == int(T0.timestamp())
    assert 'name = "run.attempt"' in logs.started["queryString"]
    assert sleeps == [2.0]


def test_first_attempts_raises_on_failed_query():
    with pytest.raises(RuntimeError):
        aws.first_attempts(FakeLogs(["Failed"], []), set(), T0, T0, sleep=lambda s: None)


def test_first_attempts_times_out():
    logs = FakeLogs(["Running"] * 10, [])
    with pytest.raises(RuntimeError):
        aws.first_attempts(logs, set(), T0, T0, sleep=lambda s: None, poll_s=1, timeout_s=3)


class FakeCloudWatch:
    def __init__(self, values):
        self.values = values
        self.request = None

    def get_metric_data(self, **kwargs):
        self.request = kwargs
        return {
            "MetricDataResults": [
                {"Id": q["Id"], "Values": self.values.get(q["Id"], [])}
                for q in kwargs["MetricDataQueries"]
            ]
        }


class SlowCloudWatch:
    """Answers with one more minute of points on each call."""

    def __init__(self, first_minute, calls_until_end):
        self.first_minute = first_minute
        self.calls = 0
        self.calls_until_end = calls_until_end

    def get_metric_data(self, **kwargs):
        self.calls += 1
        stamps = [self.first_minute + timedelta(minutes=i) for i in range(self.calls)]
        return {
            "MetricDataResults": [
                {"Id": q["Id"], "Values": [1.0] * len(stamps), "Timestamps": stamps}
                for q in kwargs["MetricDataQueries"]
            ]
        }


def test_wait_for_worker_metrics_until_the_last_minute_is_in():
    cloudwatch = SlowCloudWatch(T0, 3)
    sleeps = []
    end = T0 + timedelta(minutes=2, seconds=40)
    metrics = aws.wait_for_worker_metrics(cloudwatch, "c", "s", T0, end, sleep=sleeps.append)
    assert metrics.last_point == T0 + timedelta(minutes=2)
    assert cloudwatch.calls == 3
    assert sleeps == [30.0, 30.0]


def test_wait_for_worker_metrics_gives_up():
    cloudwatch = FakeCloudWatch({})
    sleeps = []
    metrics = aws.wait_for_worker_metrics(
        cloudwatch, "c", "s", T0, T0, wait_s=60, poll_s=30, sleep=sleeps.append
    )
    assert metrics.last_point is None
    assert sleeps == [30.0, 30.0]


def test_worker_metrics_averages_and_maximums():
    cloudwatch = FakeCloudWatch(
        {"cpu_average": [10.0, 30.0], "cpu_maximum": [40.0, 80.0], "memory_average": [20.0]}
    )
    metrics = aws.worker_metrics(cloudwatch, "c", "s", T0, T0 + timedelta(minutes=10))
    assert metrics == aws.WorkerMetrics(
        cpu_avg=20.0, cpu_max=80.0, memory_avg=20.0, memory_max=None
    )
    query = cloudwatch.request["MetricDataQueries"][0]["MetricStat"]
    assert query["Metric"]["Namespace"] == "AWS/ECS"
    assert {d["Name"]: d["Value"] for d in query["Metric"]["Dimensions"]} == {
        "ClusterName": "c",
        "ServiceName": "s",
    }


class FakeEcs:
    def describe_services(self, cluster, services):
        assert (cluster, services) == ("c", ["s"])
        return {"services": [{"desiredCount": 5, "runningCount": 3}]}


def test_sample_tasks():
    sample = aws.sample_tasks(FakeEcs(), "c", "s")
    assert (sample.desired, sample.running) == (5, 3)


class PagedLogs:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def filter_log_events(self, **kwargs):
        self.calls.append(kwargs)
        index = int(kwargs.get("nextToken", "0"))
        page = {"events": [{"message": m} for m in self.pages[index]]}
        if index + 1 < len(self.pages):
            page["nextToken"] = str(index + 1)
        return page


def test_log_messages_reads_every_page():
    logs = PagedLogs([["a", "b"], ["c"]])
    messages = list(aws.log_messages(logs, "/g", '"tool_call"', T0, T0 + timedelta(minutes=1)))
    assert messages == ["a", "b", "c"]
    assert logs.calls[0]["startTime"] == int(T0.timestamp() * 1000)
    assert logs.calls[0]["filterPattern"] == '"tool_call"'
    assert logs.calls[1]["nextToken"] == "1"
