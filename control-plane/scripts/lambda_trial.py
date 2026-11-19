"""Lambda packaging trial for the control plane (ADR 0017).

Deploys build/distributions/control-plane-lambda.zip as a throwaway Lambda function with SnapStart
next to a throwaway DynamoDB table, measures cold and warm Slack approval clicks for each memory
size and priming setting, runs one sweep, then deletes everything it made (unless --keep).

    cd control-plane && ./gradlew lambdaZip
    uv run --with boto3 python scripts/lambda_trial.py            # all four configurations
    uv run --with boto3 python scripts/lambda_trial.py --configs 1024:on

Requests go straight to the function (aws lambda invoke) as API Gateway HTTP API events; API
Gateway itself adds about 10-30 ms. A cold click is a click on a fresh environment: right after a
version is published, CLICKS_COLD clicks are sent at once, so each gets its own environment
restored from the snapshot. Times come from each invoke's REPORT log line (Restore Duration +
Duration) and from the client's wall clock.

Everything made carries the tags project=agent-platform and purpose=lambda-trial. Uses the AWS
profile "default" (never "prod"). Slack calls go to slack.com with a made-up token, so the
message update fails with invalid_auth after a real round trip (the decision is kept); nothing
is posted. Results: build/lambda-trial/results-<time>.json (git-ignored).
"""

from __future__ import annotations

import argparse
import base64
import concurrent.futures
import hashlib
import hmac
import json
import pathlib
import re
import secrets
import statistics
import sys
import time
import urllib.parse

import boto3
from botocore.exceptions import ClientError

ROOT = pathlib.Path(__file__).resolve().parent.parent
ZIP = ROOT / "build" / "distributions" / "control-plane-lambda.zip"
TABLE_JSON = ROOT / "dynamodb" / "control-plane-table.json"
SPEC = ROOT.parent / "contracts" / "examples" / "agent-spec.valid.yaml"
CLICK_FIXTURE = ROOT / "src" / "test" / "resources" / "fixtures" / "slack" / "block_actions.json"
OUT = ROOT / "build" / "lambda-trial"

REGION = "us-east-1"
HANDLER = "dev.agentplatform.controlplane.lambda.LambdaHandler::handleRequest"
TAGS = {"project": "agent-platform", "purpose": "lambda-trial"}
CHANNEL = "C0123456789"
AGENT = "trial"
CLICKS_COLD = 5
CLICKS_WARM = 20
STEP = 5


class Trial:
    def __init__(self, profile: str, arch: str):
        if profile == "prod":
            sys.exit("Refusing the prod profile.")
        self.session = boto3.Session(profile_name=profile, region_name=REGION)
        self.iam = self.session.client("iam")
        self.ddb = self.session.client("dynamodb")
        self.fn = self.session.client("lambda")
        self.logs = self.session.client("logs")
        self.arch = arch
        suffix = secrets.token_hex(3)
        self.name = f"cp-trial-{suffix}"
        self.table = f"cp-trial-{suffix}"
        self.role = f"cp-trial-{suffix}"
        # Throwaway secrets: they exist only in this function's environment and die with it.
        self.api_key = secrets.token_urlsafe(24)
        self.slack_secret = secrets.token_hex(16)
        self.github_secret = secrets.token_hex(16)
        self.made: list[str] = []

    # ---- setup and cleanup ----

    def create(self, memory: int) -> None:
        print(f"Creating table, role and function {self.name} ({self.arch})")
        definition = json.loads(TABLE_JSON.read_text())
        definition["TableName"] = self.table
        self.ddb.create_table(**definition, Tags=[{"Key": k, "Value": v} for k, v in TAGS.items()])
        self.made.append("table")

        trust = {
            "Version": "2012-10-17",
            "Statement": [{"Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"},
                           "Action": "sts:AssumeRole"}],
        }
        role_arn = self.iam.create_role(
            RoleName=self.role, AssumeRolePolicyDocument=json.dumps(trust),
            Tags=[{"Key": k, "Value": v} for k, v in TAGS.items()])["Role"]["Arn"]
        self.made.append("role")
        self.iam.attach_role_policy(
            RoleName=self.role, PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole")
        self.ddb.get_waiter("table_exists").wait(TableName=self.table)
        self.ddb.update_time_to_live(
            TableName=self.table, TimeToLiveSpecification={"Enabled": True, "AttributeName": "ttl"})
        table_arn = self.ddb.describe_table(TableName=self.table)["Table"]["TableArn"]
        self.iam.put_role_policy(RoleName=self.role, PolicyName="table", PolicyDocument=json.dumps({
            "Version": "2012-10-17",
            "Statement": [{"Effect": "Allow",
                           "Action": ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem",
                                      "dynamodb:DeleteItem", "dynamodb:Query", "dynamodb:TransactWriteItems",
                                      "dynamodb:ConditionCheckItem"],
                           "Resource": [table_arn, f"{table_arn}/index/*"]}],
        }))

        code = ZIP.read_bytes()
        for attempt in range(12):  # a new role takes a few seconds before Lambda can assume it
            try:
                self.fn.create_function(
                    FunctionName=self.name, Runtime="java21", Handler=HANDLER, Role=role_arn,
                    Code={"ZipFile": code}, MemorySize=memory, Timeout=30, Architectures=[self.arch],
                    SnapStart={"ApplyOn": "PublishedVersions"}, Environment={"Variables": self.env(True)},
                    Tags=TAGS)
                break
            except ClientError as e:
                if e.response["Error"]["Code"] != "InvalidParameterValueException" or attempt == 11:
                    raise
                time.sleep(5)
        self.made.append("function")
        self.fn.get_waiter("function_active_v2").wait(FunctionName=self.name)

    def env(self, priming: bool) -> dict[str, str]:
        return {
            "STORE_BACKEND": "dynamodb",
            "CONTROL_PLANE_TABLE": self.table,
            "QUEUE_BACKEND": "memory",
            "TENANT_API_KEY": self.api_key,
            "GITHUB_WEBHOOK_SECRET": self.github_secret,
            "SLACK_SIGNING_SECRET": self.slack_secret,
            "SLACK_BOT_TOKEN": "xoxb-trial-not-a-token",
            "SLACK_APPROVAL_CHANNEL": CHANNEL,
            "LAMBDA_PRIMING": "on" if priming else "off",
        }

    def cleanup(self) -> None:
        print("Deleting what the trial made")
        if "function" in self.made:
            self.fn.delete_function(FunctionName=self.name)  # every version with it
        if "table" in self.made:
            self.ddb.delete_table(TableName=self.table)
        if "role" in self.made:
            self.iam.delete_role_policy(RoleName=self.role, PolicyName="table")
            self.iam.detach_role_policy(
                RoleName=self.role, PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole")
            self.iam.delete_role(RoleName=self.role)
        if "function" in self.made:
            # Lambda delivers the last log lines late and would recreate a group deleted at once.
            group = f"/aws/lambda/{self.name}"
            time.sleep(30)
            try:
                self.logs.delete_log_group(logGroupName=group)
            except ClientError:
                pass
            time.sleep(15)
            if self.logs.describe_log_groups(logGroupNamePrefix=group)["logGroups"]:
                print(f"  log group {group} came back; delete it: aws logs delete-log-group --log-group-name {group}")

    def publish(self, memory: int, priming: bool) -> str:
        self.fn.update_function_configuration(
            FunctionName=self.name, MemorySize=memory, Environment={"Variables": self.env(priming)})
        self.fn.get_waiter("function_updated_v2").wait(FunctionName=self.name)
        started = time.monotonic()
        version = self.fn.publish_version(FunctionName=self.name)["Version"]
        self.fn.get_waiter("function_active_v2").wait(
            FunctionName=self.name, Qualifier=version, WaiterConfig={"Delay": 5, "MaxAttempts": 120})
        print(f"  version {version} ready in {time.monotonic() - started:.0f} s (snapshot taken)")
        return version

    # ---- requests ----

    def invoke(self, qualifier: str, event: dict) -> tuple[dict, dict, float]:
        started = time.monotonic()
        answer = self.fn.invoke(FunctionName=self.name, Qualifier=qualifier, LogType="Tail",
                                Payload=json.dumps(event).encode())
        wall = (time.monotonic() - started) * 1000
        payload = json.loads(answer["Payload"].read() or b"null")
        if answer.get("FunctionError"):
            raise RuntimeError(f"Function error: {payload}")
        log = base64.b64decode(answer.get("LogResult", "")).decode(errors="replace")
        return payload, report(log), wall

    def api(self, qualifier: str, method: str, path: str, body=None, content_type="application/json",
            key=True) -> tuple[int, dict | None]:
        headers = {"content-type": content_type}
        if key:
            headers["x-api-key"] = self.api_key
        text = body if isinstance(body, str) or body is None else json.dumps(body)
        payload, _, _ = self.invoke(qualifier, http_event(method, path, headers, text))
        raw = payload.get("body") or ""
        if payload.get("isBase64Encoded"):
            raw = base64.b64decode(raw).decode()
        return payload["statusCode"], (json.loads(raw) if raw.strip().startswith("{") else None)

    def seed(self, n: int) -> list[str]:
        """n runs WAITING_APPROVAL on the same call, made through $LATEST (no snapshot)."""
        status, _ = self.api("$LATEST", "POST", "/agents", {"name": AGENT})
        if status == 201:
            spec = SPEC.read_text().replace("name: issue-triage", f"name: {AGENT}")
            status, body = self.api("$LATEST", "POST", f"/agents/{AGENT}/versions", spec, "application/yaml")
            assert status == 201, (status, body)
        runs = []
        for _ in range(n):
            status, body = self.api("$LATEST", "POST", "/runs", {"agent": AGENT, "input": {"issue": {"number": 7}}})
            assert status == 201, (status, body)
            run_id = body["run_id"]
            for patch in ({"status": "RUNNING"},
                          {"status": "WAITING_APPROVAL", "pending_approval": {
                              "step": STEP, "tool": "github.close_issue", "arguments": {"issue_number": 7}}}):
                status, body = self.api("$LATEST", "PATCH", f"/internal/runs/{run_id}", patch, key=False)
                assert status == 200, (status, body)
            runs.append(run_id)
        return runs

    def click_event(self, run_id: str) -> dict:
        payload = json.loads(CLICK_FIXTURE.read_text())
        payload["actions"][0]["value"] = json.dumps({"run_id": run_id, "step": STEP})
        payload["container"]["channel_id"] = CHANNEL
        body = "payload=" + urllib.parse.quote_plus(json.dumps(payload))
        timestamp = str(int(time.time()))
        signature = "v0=" + hmac.new(self.slack_secret.encode(), f"v0:{timestamp}:{body}".encode(),
                                     hashlib.sha256).hexdigest()
        return http_event("POST", "/approvals/slack", {
            "content-type": "application/x-www-form-urlencoded",
            "x-slack-request-timestamp": timestamp,
            "x-slack-signature": signature,
        }, body)

    def click(self, version: str, run_id: str) -> dict:
        payload, rep, wall = self.invoke(version, self.click_event(run_id))
        assert payload["statusCode"] == 200, payload
        return {**rep, "wall_ms": round(wall, 1), "run_id": run_id}

    # ---- one configuration ----

    def measure(self, memory: int, priming: bool) -> dict:
        print(f"Configuration {memory} MB, priming {'on' if priming else 'off'}")
        cold_runs = self.seed(CLICKS_COLD)
        warm_runs = self.seed(CLICKS_WARM)
        version = self.publish(memory, priming)

        with concurrent.futures.ThreadPoolExecutor(CLICKS_COLD) as pool:
            cold = list(pool.map(lambda r: self.click(version, r), cold_runs))
        warm = [self.click(version, r) for r in warm_runs]

        for run_id in cold_runs:
            status, body = self.api(version, "GET", f"/runs/{run_id}")
            assert status == 200 and body["status"] == "QUEUED", (status, body)

        sweep, sweep_report, _ = self.invoke(version, {"sweep": "all"})
        result = {
            "memory_mb": memory, "priming": priming, "arch": self.arch, "version": version,
            "cold": cold, "warm": warm, "sweep": sweep, "sweep_report": sweep_report,
            "summary": summarize(cold, warm),
        }
        print("  " + json.dumps(result["summary"]))
        return result


def http_event(method: str, path: str, headers: dict, body: str | None) -> dict:
    event = {
        "version": "2.0", "routeKey": "$default", "rawPath": path, "rawQueryString": "",
        "headers": headers,
        "requestContext": {
            "requestId": secrets.token_hex(8), "routeKey": "$default", "stage": "$default",
            "timeEpoch": int(time.time() * 1000),
            "http": {"method": method, "path": path, "protocol": "HTTP/1.1", "sourceIp": "127.0.0.1",
                     "userAgent": "lambda-trial"},
        },
        "isBase64Encoded": False,
    }
    if body is not None:
        event["body"] = body
    return event


def report(log: str) -> dict:
    line = next((l for l in log.splitlines() if l.startswith("REPORT")), "")
    def number(label: str) -> float | None:
        found = re.search(rf"\t{label}: ([\d.]+) ms", line)
        return float(found.group(1)) if found else None
    return {"duration_ms": number("Duration"), "restore_ms": number("Restore Duration"),
            "init_ms": number("Init Duration")}


def summarize(cold: list[dict], warm: list[dict]) -> dict:
    def stats(values: list[float]) -> dict:
        values = sorted(values)
        return {"median": round(statistics.median(values), 1), "max": round(values[-1], 1)}
    restored = [c for c in cold if c["restore_ms"] is not None]
    return {
        "cold_restored": f"{len(restored)}/{len(cold)}",
        "cold_restore_ms": stats([c["restore_ms"] for c in restored]) if restored else None,
        "cold_duration_ms": stats([c["duration_ms"] for c in cold]),
        "cold_total_ms": stats([(c["restore_ms"] or 0) + c["duration_ms"] for c in cold]),
        "cold_wall_ms": stats([c["wall_ms"] for c in cold]),
        "warm_duration_ms": {**stats([w["duration_ms"] for w in warm]),
                             "p95": round(sorted(w["duration_ms"] for w in warm)[int(len(warm) * 0.95) - 1], 1)},
        "warm_wall_ms": stats([w["wall_ms"] for w in warm]),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--profile", default="default")
    parser.add_argument("--arch", default="arm64", choices=["arm64", "x86_64"])
    parser.add_argument("--configs", default="1024:on,1024:off,2048:on,2048:off",
                        help="memory:priming pairs, comma-separated")
    parser.add_argument("--keep", action="store_true", help="leave the function, table and role")
    args = parser.parse_args()
    if not ZIP.exists():
        sys.exit(f"{ZIP} is missing: run ./gradlew lambdaZip first")
    configs = [(int(m), p == "on") for m, p in (c.split(":") for c in args.configs.split(","))]

    trial = Trial(args.profile, args.arch)
    results = []
    try:
        trial.create(configs[0][0])
        for memory, priming in configs:
            results.append(trial.measure(memory, priming))
    finally:
        if results:
            OUT.mkdir(parents=True, exist_ok=True)
            path = OUT / f"results-{time.strftime('%Y%m%d-%H%M%S')}.json"
            path.write_text(json.dumps(results, indent=2))
            print(f"Results: {path}")
        if args.keep:
            print(f"Kept: function, table and role {trial.name}")
        else:
            trial.cleanup()


if __name__ == "__main__":
    main()
