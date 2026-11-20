"""End-to-end check on AWS, like `make demo` locally: publish the example spec (stub model) as its
own agent `demo-stub`, start a run, wait for SUCCEEDED, find the run's trace in X-Ray (its spans in
the `aws/spans` log group, Transaction Search; ADR 0022), then wait for the workers to scale back
to 0.

The live agent `issue-triage` is never touched: the spec's name becomes `demo-stub` and its
trigger `manual.demo.run`, which no webhook sends. The stub's tool calls need the gateway's fake
connector, so the script refuses unless `gateway_connectors` (terraform output) includes `fake`:
set `load_test = true` (or `gateway_connectors = "fake"`) and apply first.

    cd infra
    uv run --with boto3 python scripts/aws_demo.py   # --no-trace, --no-scale-in to skip those

Reads the API address from `terraform output` and the tenant API key from SSM (never printed).
Reports how long the run waited for a worker (scale-out from 0 is the slow part, ADR 0007).
Costs a few cents: one Fargate task for some minutes. Uses the profile "default" (never "prod").
"""

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import boto3

SPANS_LOG_GROUP = "aws/spans"
WORKER_SPANS = ("run.attempt", "model.call", "tool.call", "checkpoint")
TRACE_WAIT_SECONDS = 240

INFRA = Path(__file__).resolve().parent.parent
SPEC = INFRA.parent / "contracts" / "examples" / "agent-spec.valid.yaml"
AGENT = "demo-stub"
TRIGGER = "manual.demo.run"  # no webhook sends it: GitHub events never start this agent
RUN_INPUT = {
    "repository": {"full_name": "acme/payments"},
    "issue": {"number": 42, "title": "Refund fails for EUR cards"},
}


def output(name: str):
    raw = subprocess.run(
        ["terraform", f"-chdir={INFRA / 'envs' / 'dev'}", "output", "-json", name],
        check=True, capture_output=True, text=True,
    ).stdout
    return json.loads(raw)


class Api:
    def __init__(self, url: str, key: str) -> None:
        self.url = url.rstrip("/")
        self._key = key

    def call(self, method: str, path: str, body: bytes | None = None,
             content_type: str = "application/json", ok=(200, 201)) -> tuple[int, dict]:
        request = urllib.request.Request(self.url + path, data=body, method=method, headers={
            "X-Api-Key": self._key, "Content-Type": content_type})
        try:
            with urllib.request.urlopen(request, timeout=35) as response:
                status, text = response.status, response.read().decode()
        except urllib.error.HTTPError as e:
            status, text = e.code, e.read().decode()
        if status not in ok:
            sys.exit(f"{method} {path} answered {status}: {text[:500]}")
        return status, (json.loads(text) if text else {})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--profile", default="default")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--timeout", type=int, default=1200, help="seconds to wait for the run")
    parser.add_argument("--no-scale-in", action="store_true", help="do not wait for 0 workers")
    parser.add_argument("--no-trace", action="store_true", help="do not look for the run's trace")
    args = parser.parse_args()
    if args.profile == "prod":
        sys.exit("This project never uses the prod profile.")

    connectors = [c.strip() for c in output("gateway_connectors").split(",")]
    if "fake" not in connectors:
        sys.exit(f"The gateway runs {', '.join(connectors)}: the stub's tool calls need the fake "
                 "connector. Set load_test = true (or gateway_connectors = \"fake\") in "
                 "terraform.tfvars and apply, then run this again.")
    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    key_name = output("secret_parameters")["TENANT_API_KEY"]
    key = session.client("ssm").get_parameter(Name=key_name, WithDecryption=True)["Parameter"]
    if key["Version"] == 1:
        sys.exit(f"{key_name} still holds Terraform's placeholder: put_secrets.py --generate TENANT_API_KEY")
    api = Api(output("api_url"), key["Value"])
    service = output("worker_service")
    ecs = session.client("ecs")

    print(f"API {api.url}")
    print(f"1. Create agent {AGENT} (409 if it already exists)")
    api.call("POST", "/agents", json.dumps({"name": AGENT}).encode(), ok=(201, 409))
    print(f"2. Publish {SPEC.relative_to(INFRA.parent)} as {AGENT} (trigger {TRIGGER})")
    _, published = api.call("POST", f"/agents/{AGENT}/versions", demo_spec(), "application/yaml")
    print(f"   version {published.get('version')}")
    print("3. Start a run")
    started = time.monotonic()
    started_ms = int((time.time() - 60) * 1000)
    _, run = api.call("POST", "/runs", json.dumps({"agent": AGENT, "input": RUN_INPUT}).encode())
    run_id = run["run_id"]
    print(f"   {run_id}")

    print(f"4. Wait for a worker and the run (up to {args.timeout} s)")
    picked_up = None
    while True:
        _, run = api.call("GET", f"/runs/{run_id}")
        elapsed = time.monotonic() - started
        if picked_up is None and run["status"] != "QUEUED":
            picked_up = elapsed
            print(f"   picked up after {picked_up:.0f} s (status {run['status']})")
        if run["status"] in ("SUCCEEDED", "FAILED", "CANCELLED", "WAITING_APPROVAL"):
            break
        if elapsed > args.timeout:
            sys.exit(f"Run still {run['status']} after {args.timeout} s")
        time.sleep(10)
    print(f"   {json.dumps({k: run.get(k) for k in ('status', 'steps', 'cost_usd', 'output', 'failure')})}")
    if run["status"] != "SUCCEEDED":
        sys.exit(f"Run ended {run['status']}")
    print(f"Run SUCCEEDED in {time.monotonic() - started:.0f} s ({picked_up:.0f} s waiting for a worker)")

    if not args.no_trace:
        print(f"5. Find trace {run['trace_id']} in {SPANS_LOG_GROUP} (up to {TRACE_WAIT_SECONDS} s)")
        find_trace(session.client("logs"), run["trace_id"], started_ms)
    if args.no_scale_in:
        return 0
    print("6. Wait for the workers to scale back to 0 (about 5-8 minutes)")
    finished = time.monotonic()
    while True:
        described = ecs.describe_services(cluster=service["cluster"], services=[service["service"]])
        current = described["services"][0]
        if current["desiredCount"] == 0 and current["runningCount"] == 0:
            print(f"Workers back to 0 after {time.monotonic() - finished:.0f} s")
            return 0
        if time.monotonic() - finished > 1200:
            sys.exit("Workers still running 20 minutes after the run")
        time.sleep(30)


def demo_spec() -> bytes:
    """The example spec with this script's agent name and a trigger no webhook sends."""
    text = SPEC.read_text()
    for key, value in (("name", AGENT), ("trigger", TRIGGER)):
        lines = [line for line in text.splitlines() if line.startswith(f"{key}: ")]
        if len(lines) != 1:
            sys.exit(f"{SPEC.name}: expected one top-level '{key}:' line, found {len(lines)}")
        text = text.replace(lines[0], f"{key}: {value}")
    return text.encode()


def find_trace(logs, trace_id: str, since_ms: int) -> None:
    """Waits until the worker's spans of the run are in `aws/spans` and prints how many of each
    name arrived, per service (names only: never attributes). Spans take about 20-30 s to show."""
    deadline = time.monotonic() + TRACE_WAIT_SECONDS
    while True:
        counts: dict[tuple[str, str], int] = {}
        for event in span_events(logs, trace_id, since_ms):
            span = json.loads(event["message"])
            service = span.get("resource", {}).get("attributes", {}).get("service.name", "?")
            key = (service, span.get("name", "?"))
            counts[key] = counts.get(key, 0) + 1
        missing = [n for n in WORKER_SPANS if ("worker", n) not in counts]
        if not missing:
            for (service, name), count in sorted(counts.items()):
                print(f"   {service:<14} {name:<16} {count}")
            print(f"Trace {trace_id}: {sum(counts.values())} spans (the control plane's run.start "
                  "is not uploaded, ADR 0022)")
            return
        if time.monotonic() > deadline:
            sys.exit(f"Trace {trace_id}: no {', '.join(missing)} span after {TRACE_WAIT_SECONDS} s "
                     f"(found {sorted(counts)}); check the worker log for exporter errors")
        time.sleep(15)


def span_events(logs, trace_id: str, since_ms: int):
    kwargs = {"logGroupName": SPANS_LOG_GROUP, "startTime": since_ms,
              "filterPattern": f'{{ $.traceId = "{trace_id}" }}'}
    while True:
        page = logs.filter_log_events(**kwargs)
        yield from page["events"]
        if not page.get("nextToken"):
            return
        kwargs["nextToken"] = page["nextToken"]


if __name__ == "__main__":
    sys.exit(main())
