"""What the driver reads from AWS and Terraform: settings, span start times, worker metrics.

Read only: the driver never changes AWS resources (load-test mode is a Terraform apply the user
runs, ADR 0024). Clients are passed in, so tests use fakes.
"""

import json
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
DEV_ENV = REPO / "infra" / "envs" / "dev"
SPANS_LOG_GROUP = "aws/spans"
LAMBDA_CONCURRENCY_QUOTA = "L-B99A9384"  # Lambda "Concurrent executions"
# `queue.receive_count` is the run message's receive count on each attempt: above 1 means the run
# was taken over (a worker died or lost its lease). Counting spans is not enough: a killed worker
# never uploads the spans it still holds, so its attempt leaves none.
ATTEMPTS_QUERY = (
    'filter name = "run.attempt" '
    "| stats min(startTimeUnixNano) as first_start, count(*) as attempts, "
    "max(`attributes.queue.receive_count`) as receives by traceId "
    "| limit 10000"
)


class NotReady(Exception):
    """The deployment is not in load-test mode; the message says what to do."""


def terraform_output(name: str, env_dir: Path = DEV_ENV):
    raw = subprocess.run(
        ["terraform", f"-chdir={env_dir}", "output", "-json", name],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return json.loads(raw)


@dataclass(frozen=True)
class Target:
    api_url: str
    key_parameter: str
    cluster: str
    service: str
    gateway_log_group: str


def read_target(output: Callable[[str], object] = terraform_output) -> Target:
    """The dev deployment's settings; refuses unless load-test mode is on (fake connector, so the
    stub's tool calls never reach GitHub; throttle and worker limits sized for a test)."""
    if output("load_test") is not True:
        raise NotReady(
            "load_test is off: set load_test = true in infra/envs/dev/terraform.tfvars and "
            "apply, then run this again (set it back to false after the test)."
        )
    connectors = [c.strip() for c in str(output("gateway_connectors")).split(",")]
    if connectors != ["fake"]:
        raise NotReady(
            f"the gateway runs {', '.join(connectors)}: a load test needs only the fake "
            "connector. Apply with load_test = true first."
        )
    service = output("worker_service")
    return Target(
        api_url=str(output("api_url")).rstrip("/"),
        key_parameter=output("secret_parameters")["TENANT_API_KEY"],
        cluster=service["cluster"],
        service=service["service"],
        gateway_log_group=f"/aws/lambda/{output('functions')['gateway']}",
    )


def tenant_key(ssm, parameter: str) -> str:
    """The tenant API key from SSM. Never printed or written to results."""
    value = ssm.get_parameter(Name=parameter, WithDecryption=True)["Parameter"]
    if value["Version"] == 1:
        raise NotReady(f"{parameter} still holds Terraform's placeholder (put_secrets.py)")
    return value["Value"]


def lambda_concurrency(quotas) -> float | None:
    """The account's Lambda concurrent executions limit, None if it cannot be read."""
    try:
        quota = quotas.get_service_quota(ServiceCode="lambda", QuotaCode=LAMBDA_CONCURRENCY_QUOTA)
    except Exception:  # noqa: BLE001 - only a warning depends on it
        return None
    return quota["Quota"]["Value"]


@dataclass(frozen=True)
class FirstAttempt:
    started_at: datetime
    attempts: int  # run.attempt spans found (a killed worker's attempt has none)
    receives: int = 1  # highest queue receive count seen on the run's attempts


def first_attempts(
    logs,
    trace_ids: set[str],
    start: datetime,
    end: datetime,
    *,
    sleep: Callable[[float], None] = time.sleep,
    poll_s: float = 2.0,
    timeout_s: float = 120.0,
) -> dict[str, FirstAttempt]:
    """Earliest "run.attempt" span start and number of attempts per trace, for the given traces,
    from one Logs Insights query over `aws/spans` (spans of other runs in the window are
    ignored). A trace with no span yet is left out."""
    query_id = logs.start_query(
        logGroupName=SPANS_LOG_GROUP,
        startTime=int(start.timestamp()),
        endTime=int(end.timestamp()) + 1,
        queryString=ATTEMPTS_QUERY,
    )["queryId"]
    waited = 0.0
    while True:
        result = logs.get_query_results(queryId=query_id)
        if result["status"] == "Complete":
            break
        if result["status"] in ("Failed", "Cancelled", "Timeout", "Unknown"):
            raise RuntimeError(f"Logs Insights query ended {result['status']}")
        if waited >= timeout_s:
            raise RuntimeError(f"Logs Insights query still {result['status']} after {timeout_s} s")
        sleep(poll_s)
        waited += poll_s
    found: dict[str, FirstAttempt] = {}
    for row in result["results"]:
        fields = {f["field"]: f["value"] for f in row}
        trace_id = fields.get("traceId")
        if trace_id not in trace_ids:
            continue
        nanos = int(float(fields["first_start"]))
        found[trace_id] = FirstAttempt(
            started_at=datetime.fromtimestamp(nanos / 1e9, UTC),
            attempts=int(float(fields["attempts"])),
            receives=int(float(fields.get("receives") or 1)),
        )
    return found


@dataclass(frozen=True)
class WorkerMetrics:
    cpu_avg: float | None
    cpu_max: float | None
    memory_avg: float | None
    memory_max: float | None
    last_point: datetime | None = None  # start of the newest one-minute point


def worker_metrics(
    cloudwatch, cluster: str, service: str, start: datetime, end: datetime
) -> WorkerMetrics:
    """`AWS/ECS` CPU and memory utilization (percent of the task size) of the worker service,
    one-minute averages and maximums over the window. CloudWatch publishes them a minute or two
    late: `last_point` says how far the answer reaches (`wait_for_worker_metrics`)."""
    dimensions = [
        {"Name": "ClusterName", "Value": cluster},
        {"Name": "ServiceName", "Value": service},
    ]
    queries = []
    for metric, short in (("CPUUtilization", "cpu"), ("MemoryUtilization", "memory")):
        for stat in ("Average", "Maximum"):
            queries.append(
                {
                    "Id": f"{short}_{stat.lower()}",
                    "MetricStat": {
                        "Metric": {
                            "Namespace": "AWS/ECS",
                            "MetricName": metric,
                            "Dimensions": dimensions,
                        },
                        "Period": 60,
                        "Stat": stat,
                    },
                }
            )
    response = cloudwatch.get_metric_data(
        MetricDataQueries=queries,
        StartTime=start - timedelta(minutes=1),
        EndTime=end + timedelta(minutes=1),
    )
    values = {r["Id"]: r["Values"] for r in response["MetricDataResults"]}
    stamps = [s for r in response["MetricDataResults"] for s in r.get("Timestamps", [])]

    def mean(name: str) -> float | None:
        points = values.get(name) or []
        return sum(points) / len(points) if points else None

    def top(name: str) -> float | None:
        points = values.get(name) or []
        return max(points) if points else None

    return WorkerMetrics(
        cpu_avg=mean("cpu_average"),
        cpu_max=top("cpu_maximum"),
        memory_avg=mean("memory_average"),
        memory_max=top("memory_maximum"),
        last_point=max(stamps) if stamps else None,
    )


def wait_for_worker_metrics(
    cloudwatch,
    cluster: str,
    service: str,
    start: datetime,
    end: datetime,
    *,
    wait_s: float = 300.0,
    poll_s: float = 30.0,
    sleep: Callable[[float], None] = time.sleep,
) -> WorkerMetrics:
    """`worker_metrics`, asked again every `poll_s` until a point covers the minute `end` falls
    in, or `wait_s` passes (then the answer is what CloudWatch has)."""
    last_minute = end.replace(second=0, microsecond=0)
    waited = 0.0
    while True:
        metrics = worker_metrics(cloudwatch, cluster, service, start, end)
        reached = metrics.last_point is not None and metrics.last_point >= last_minute
        if reached or waited >= wait_s:
            return metrics
        sleep(poll_s)
        waited += poll_s


def log_messages(logs, log_group: str, pattern: str, start: datetime, end: datetime):
    """Messages of the log events matching `pattern` in the window, every page."""
    kwargs = {
        "logGroupName": log_group,
        "startTime": int(start.timestamp() * 1000),
        "endTime": int(end.timestamp() * 1000),
        "filterPattern": pattern,
    }
    while True:
        page = logs.filter_log_events(**kwargs)
        for event in page["events"]:
            yield event["message"]
        token = page.get("nextToken")
        if not token:
            return
        kwargs["nextToken"] = token


@dataclass(frozen=True)
class TaskSample:
    at: datetime
    desired: int
    running: int


def sample_tasks(ecs, cluster: str, service: str) -> TaskSample:
    current = ecs.describe_services(cluster=cluster, services=[service])["services"][0]
    return TaskSample(
        at=datetime.now(UTC), desired=current["desiredCount"], running=current["runningCount"]
    )


# --- M8: tenant limits (ADR 0026, ADR 0027) ------------------------------------------------

KEY_DIR = Path.home() / ".config" / "agent-platform"
# One row per worker attempt at a run, with how it ended (`run.status`: QUEUED after a start put
# off at the tenant's cap or a set-aside) and the run message's receive count.
ATTEMPT_ROWS_QUERY = (
    'filter name = "run.attempt" '
    "| fields traceId, spanId, startTimeUnixNano, endTimeUnixNano, "
    "`attributes.run.status` as status, `attributes.queue.receive_count` as receives "
    "| limit 10000"
)
# Model calls per attempt (their parent span): an attempt that called the model held a slot.
MODEL_CALLS_QUERY = (
    'filter name = "model.call" '
    "| stats count(*) as calls, min(startTimeUnixNano) as first_start, "
    "max(endTimeUnixNano) as last_end by traceId, parentSpanId "
    "| limit 10000"
)


# The worker's control-plane calls in an attempt (worker image 2026-11-21 and later): the start
# PATCH that took a slot and the PATCH or defer that freed it.
CONTROL_PLANE_QUERY = (
    'filter name = "control_plane.update" or name = "control_plane.defer" '
    "| fields traceId, parentSpanId, name, startTimeUnixNano, endTimeUnixNano, "
    "`attributes.run.status` as requested, `attributes.outcome` as outcome "
    "| limit 10000"
)
# Statuses whose PATCH frees a slot (the run leaves RUNNING).
LEAVES_RUNNING = frozenset({"SUCCEEDED", "FAILED", "WAITING_APPROVAL"})


def key_file(tenant: str, directory: Path = KEY_DIR) -> str:
    """A test tenant's API key from `<directory>/<tenant>.key` (mode 0600). Never printed or
    written to results."""
    path = directory / f"{tenant}.key"
    if not path.exists():
        raise NotReady(f"no key file for {tenant}: {path} (create the tenant first, see AGENTS.md)")
    if path.stat().st_mode & 0o077:
        raise NotReady(f"{path} is readable by others: chmod 600 it")
    key = path.read_text().strip()
    if not key:
        raise NotReady(f"{path} is empty")
    return key


def run_query(
    logs,
    query: str,
    start: datetime,
    end: datetime,
    *,
    sleep: Callable[[float], None] = time.sleep,
    poll_s: float = 2.0,
    timeout_s: float = 120.0,
) -> list[dict[str, str]]:
    """One Logs Insights query over `aws/spans`; each result row as {field: value}."""
    query_id = logs.start_query(
        logGroupName=SPANS_LOG_GROUP,
        startTime=int(start.timestamp()),
        endTime=int(end.timestamp()),
        queryString=query,
    )["queryId"]
    waited = 0.0
    while True:
        result = logs.get_query_results(queryId=query_id)
        if result["status"] == "Complete":
            return [{f["field"]: f["value"] for f in row} for row in result["results"]]
        if result["status"] in ("Failed", "Cancelled", "Timeout", "Unknown"):
            raise RuntimeError(f"Logs Insights query ended {result['status']}")
        if waited >= timeout_s:
            raise RuntimeError(f"Logs Insights query still {result['status']} after {timeout_s} s")
        sleep(poll_s)
        waited += poll_s


def windows(start: datetime, end: datetime, seconds: int) -> list[tuple[datetime, datetime]]:
    """[start, end) cut into back-to-back pieces of `seconds` (the last may be shorter), on whole
    seconds as Logs Insights takes them."""
    start = start.replace(microsecond=0)
    pieces = []
    at = start
    while at < end:
        nxt = min(at + timedelta(seconds=seconds), end)
        pieces.append((at, nxt))
        at = nxt
    return pieces


def windowed_rows(
    logs, query: str, start: datetime, end: datetime, *, window_s: int = 60, **kwargs
) -> list[dict[str, str]]:
    """`run_query` per window, so no answer reaches Logs Insights' 10,000-row limit. A row on a
    window's edge can come twice; callers key rows by span."""
    rows = []
    for a, b in windows(start, end, window_s):
        found = run_query(logs, query, a, b, **kwargs)
        if len(found) >= 10_000:
            raise RuntimeError(f"{len(found)} rows in {a}..{b}: use a smaller window")
        rows.extend(found)
    return rows


def _nanos(value: str) -> datetime:
    return datetime.fromtimestamp(int(float(value)) / 1e9, UTC)


@dataclass(frozen=True)
class Attempt:
    """One worker attempt at a run (its "run.attempt" span) and the model calls it made."""

    trace_id: str
    span_id: str
    started_at: datetime
    ended_at: datetime
    status: str  # what the attempt ended with: QUEUED (put off or set aside), SUCCEEDED, ...
    receives: int
    model_calls: int = 0
    first_call_at: datetime | None = None
    last_call_end: datetime | None = None
    # The slot, bracketed by the worker's control-plane call spans (None without them): taken
    # inside the start PATCH (between `start_sent` and `start_answered`), freed inside the final
    # PATCH or defer (between `free_sent` and `free_answered`).
    slot: "SlotCalls | None" = None


@dataclass(frozen=True)
class SlotCalls:
    start_sent: datetime
    start_answered: datetime
    free_sent: datetime
    free_answered: datetime

    @property
    def inner(self) -> tuple[datetime, datetime]:
        """Always inside the slot's time: from the start answer to sending the freeing call."""
        return self.start_answered, max(self.start_answered, self.free_sent)

    @property
    def outer(self) -> tuple[datetime, datetime]:
        """Always contains the slot's time: from sending the start to the freeing answer."""
        return self.start_sent, self.free_answered


def slot_times(calls: list[dict[str, str]]) -> SlotCalls | None:
    """The calls that took and freed an attempt's slot: the first PATCH to RUNNING that answered
    ok, and the last call after it that moves the run out of RUNNING (a PATCH to a final or
    waiting status, or defer). None if either is missing."""
    ordered = sorted(calls, key=lambda c: int(float(c["startTimeUnixNano"])))
    start = next(
        (
            c
            for c in ordered
            if c.get("name") == "control_plane.update"
            and c.get("requested") == "RUNNING"
            and c.get("outcome") == "ok"
        ),
        None,
    )
    if start is None:
        return None
    frees = [
        c
        for c in ordered
        if (c.get("name") == "control_plane.defer" or c.get("requested") in LEAVES_RUNNING)
        and int(float(c["startTimeUnixNano"])) >= int(float(start["startTimeUnixNano"]))
    ]
    if not frees:
        return None
    free = frees[-1]
    return SlotCalls(
        start_sent=_nanos(start["startTimeUnixNano"]),
        start_answered=_nanos(start["endTimeUnixNano"]),
        free_sent=_nanos(free["startTimeUnixNano"]),
        free_answered=_nanos(free["endTimeUnixNano"]),
    )


def attempts(logs, trace_ids: set[str], start: datetime, end: datetime, **kwargs) -> list[Attempt]:
    """Every "run.attempt" span of the given traces in the window, with its model calls."""
    calls: dict[str, dict[str, str]] = {}
    for row in windowed_rows(logs, MODEL_CALLS_QUERY, start, end, **kwargs):
        if row.get("traceId") not in trace_ids:
            continue
        parent = row["parentSpanId"]
        seen = calls.get(parent)
        if seen is None:
            calls[parent] = row
        else:  # the same attempt cut by a window edge: merge
            calls[parent] = {
                **seen,
                "calls": str(int(float(seen["calls"])) + int(float(row["calls"]))),
                "first_start": str(
                    min(int(float(seen["first_start"])), int(float(row["first_start"])))
                ),
                "last_end": str(max(int(float(seen["last_end"])), int(float(row["last_end"])))),
            }
    control: dict[str, dict[tuple[str, str], dict[str, str]]] = {}
    for row in windowed_rows(logs, CONTROL_PLANE_QUERY, start, end, **kwargs):
        if row.get("traceId") not in trace_ids:
            continue
        key = (row.get("name", ""), row["startTimeUnixNano"])  # a row twice at a window edge
        control.setdefault(row["parentSpanId"], {})[key] = row
    found: dict[str, Attempt] = {}
    for row in windowed_rows(logs, ATTEMPT_ROWS_QUERY, start, end, **kwargs):
        if row.get("traceId") not in trace_ids or row["spanId"] in found:
            continue
        made = calls.get(row["spanId"])
        slot = slot_times(list(control.get(row["spanId"], {}).values()))
        found[row["spanId"]] = Attempt(
            trace_id=row["traceId"],
            span_id=row["spanId"],
            started_at=_nanos(row["startTimeUnixNano"]),
            ended_at=_nanos(row["endTimeUnixNano"]),
            status=row.get("status") or "",
            receives=int(float(row.get("receives") or 1)),
            model_calls=int(float(made["calls"])) if made else 0,
            first_call_at=_nanos(made["first_start"]) if made else None,
            last_call_end=_nanos(made["last_end"]) if made else None,
            slot=slot,
        )
    return list(found.values())


@dataclass(frozen=True)
class QueueMetrics:
    visible_max: float | None  # messages waiting, all tenants
    visible_quiet_max: float | None  # messages waiting of tenants SQS does not see as noisy


def queue_metrics(cloudwatch, queue_name: str, start: datetime, end: datetime) -> QueueMetrics:
    """Highest one-minute `ApproximateNumberOfMessagesVisible` and its fair-queue counterpart
    `...InQuietGroups` over the window."""
    queries = [
        {
            "Id": short,
            "MetricStat": {
                "Metric": {
                    "Namespace": "AWS/SQS",
                    "MetricName": metric,
                    "Dimensions": [{"Name": "QueueName", "Value": queue_name}],
                },
                "Period": 60,
                "Stat": "Maximum",
            },
        }
        for short, metric in (
            ("visible", "ApproximateNumberOfMessagesVisible"),
            ("quiet", "ApproximateNumberOfMessagesVisibleInQuietGroups"),
        )
    ]
    response = cloudwatch.get_metric_data(
        MetricDataQueries=queries,
        StartTime=start - timedelta(minutes=1),
        EndTime=end + timedelta(minutes=1),
    )
    values = {r["Id"]: r["Values"] for r in response["MetricDataResults"]}
    return QueueMetrics(
        visible_max=max(values.get("visible") or [], default=None),
        visible_quiet_max=max(values.get("quiet") or [], default=None),
    )
