"""End-to-end check on AWS, like `make demo` locally: publish the example spec (stub model), start
a run, wait for SUCCEEDED, then wait for the workers to scale back to 0.

    cd infra
    uv run --with boto3 python scripts/aws_demo.py              # --no-scale-in to skip the wait

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

INFRA = Path(__file__).resolve().parent.parent
SPEC = INFRA.parent / "contracts" / "examples" / "agent-spec.valid.yaml"
AGENT = "issue-triage"
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
    args = parser.parse_args()
    if args.profile == "prod":
        sys.exit("This project never uses the prod profile.")

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    key_name = output("secret_parameters")["TENANT_API_KEY"]
    key = session.client("ssm").get_parameter(Name=key_name, WithDecryption=True)["Parameter"]
    if key["Version"] == 1:
        sys.exit(f"{key_name} still holds Terraform's placeholder: put_secrets.py --generate TENANT_API_KEY")
    api = Api(output("api_url"), key["Value"])
    service = output("worker_service")
    ecs = session.client("ecs")

    print(f"API {api.url}")
    print("1. Create agent issue-triage (409 if it already exists)")
    api.call("POST", "/agents", json.dumps({"name": AGENT}).encode(), ok=(201, 409))
    print(f"2. Publish {SPEC.relative_to(INFRA.parent)}")
    _, published = api.call("POST", f"/agents/{AGENT}/versions", SPEC.read_bytes(), "application/yaml")
    print(f"   version {published.get('version')}")
    print("3. Start a run")
    started = time.monotonic()
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

    if args.no_scale_in:
        return 0
    print("5. Wait for the workers to scale back to 0 (about 5-8 minutes)")
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


if __name__ == "__main__":
    sys.exit(main())
