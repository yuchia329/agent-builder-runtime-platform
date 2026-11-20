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
