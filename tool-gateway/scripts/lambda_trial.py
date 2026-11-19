"""Lambda trial for the tool gateway (ADR 0019): deploys build/gateway-lambda.zip as a throwaway
function with the Lambda Web Adapter layer, sends it API Gateway events, then deletes everything.

    cd tool-gateway && uv run python scripts/lambda_zip.py
    uv run python scripts/lambda_trial.py                 # --keep to leave it, --profile default

Checks, each through the real function:
1. GET /health answers 200 (the first call is the cold start: Lambda init + uvicorn + app);
2. POST /mcp without a token answers 401;
3. POST /mcp `initialize` with a run token signed by the dev KMS key answers 200: the gateway read
   the public key from KMS at startup (RUN_TOKEN_KMS_KEY_ID) and the MCP server runs on Lambda;
4. ten more GET /health for warm times.

Requests go straight to the function (lambda invoke) as API Gateway HTTP API (2.0) events. Uses
the profile "default" (never "prod"), the fake connector, and the KMS key
alias/agent-platform-dev-run-token (one kms:Sign, about $0.00003). Everything made is tagged
project=agent-platform, purpose=lambda-trial. Results: build/lambda-trial/results-<time>.json.
"""

import argparse
import base64
import contextlib
import hashlib
import json
import re
import secrets
import statistics
import sys
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

ROOT = Path(__file__).resolve().parent.parent
ZIP = ROOT / "build" / "gateway-lambda.zip"
LAYER = "arn:aws:lambda:{region}:753240598075:layer:LambdaAdapterLayerArm64:30"
KMS_ALIAS = "alias/agent-platform-dev-run-token"
TAGS = {"project": "agent-platform", "purpose": "lambda-trial"}
REPORT = re.compile(r"(Init Duration|Duration|Billed Duration|Max Memory Used): ([\d.]+)")


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def event(method: str, path: str, headers: dict[str, str] | None = None, body: str | None = None):
    now = int(time.time() * 1000)
    return {
        "version": "2.0",
        "routeKey": "$default",
        "rawPath": path,
        "rawQueryString": "",
        "headers": {k.lower(): v for k, v in (headers or {}).items()} | {"host": "trial.local"},
        "requestContext": {
            "accountId": "000000000000",
            "apiId": "trial",
            "domainName": "trial.local",
            "domainPrefix": "trial",
            "http": {
                "method": method,
                "path": path,
                "protocol": "HTTP/1.1",
                "sourceIp": "127.0.0.1",
                "userAgent": "lambda-trial",
            },
            "requestId": secrets.token_hex(8),
            "routeKey": "$default",
            "stage": "$default",
            "time": time.strftime("%d/%b/%Y:%H:%M:%S +0000", time.gmtime()),
            "timeEpoch": now,
        },
        "body": body,
        "isBase64Encoded": False,
    }


class Trial:
    def __init__(self, profile: str, region: str) -> None:
        session = boto3.Session(profile_name=profile, region_name=region)
        self.region = region
        self.fn = session.client("lambda")
        self.iam = session.client("iam")
        self.kms = session.client("kms")
        self.logs = session.client("logs")
        suffix = secrets.token_hex(3)
        self.name = f"gw-trial-{suffix}"
        self.role = f"gw-trial-{suffix}"
        self.made: list[str] = []

    def create(self) -> None:
        key_arn = self.kms.describe_key(KeyId=KMS_ALIAS)["KeyMetadata"]["Arn"]
        trust = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": "sts:AssumeRole",
                    "Principal": {"Service": "lambda.amazonaws.com"},
                }
            ],
        }
        arn = self.iam.create_role(
            RoleName=self.role,
            AssumeRolePolicyDocument=json.dumps(trust),
            Tags=[{"Key": k, "Value": v} for k, v in TAGS.items()],
        )["Role"]["Arn"]
        self.made.append("role")
        self.iam.attach_role_policy(
            RoleName=self.role,
            PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
        )
        self.iam.put_role_policy(
            RoleName=self.role,
            PolicyName="kms",
            PolicyDocument=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {"Effect": "Allow", "Action": "kms:GetPublicKey", "Resource": key_arn}
                    ],
                }
            ),
        )
        print(f"Creating role and function {self.name} (python3.12, arm64, Lambda Web Adapter)")
        for attempt in range(10):  # a new role takes a few seconds to be usable by Lambda
            try:
                self.fn.create_function(
                    FunctionName=self.name,
                    Runtime="python3.12",
                    Architectures=["arm64"],
                    Role=arn,
                    Handler="run.sh",
                    Timeout=30,
                    MemorySize=1024,
                    Code={"ZipFile": ZIP.read_bytes()},
                    Layers=[LAYER.format(region=self.region)],
                    Environment={
                        "Variables": {
                            "AWS_LAMBDA_EXEC_WRAPPER": "/opt/bootstrap",
                            "AWS_LWA_READINESS_CHECK_PATH": "/health",
                            "RUN_TOKEN_KMS_KEY_ID": KMS_ALIAS,
                            "CONNECTORS": "fake",
                        }
                    },
                    Tags=TAGS,
                )
                break
            except ClientError as e:
                if "role" in str(e).lower() and attempt < 9:
                    time.sleep(3)
                    continue
                raise
        self.made.append("function")
        self.fn.get_waiter("function_active_v2").wait(FunctionName=self.name)

    def invoke(self, request: dict) -> tuple[dict, dict, float]:
        started = time.monotonic()
        answer = self.fn.invoke(
            FunctionName=self.name, LogType="Tail", Payload=json.dumps(request).encode()
        )
        wall = (time.monotonic() - started) * 1000
        payload = json.loads(answer["Payload"].read() or b"null")
        tail = base64.b64decode(answer.get("LogResult", "")).decode(errors="replace")
        report = {k: float(v) for k, v in REPORT.findall(tail)}
        if answer.get("FunctionError"):
            raise RuntimeError(f"Function error: {payload}\n{tail[-2000:]}")
        return payload, report, wall

    def run_token(self) -> str:
        now = int(time.time())
        claims = json.loads(
            (ROOT.parent / "contracts" / "examples" / "run-token.valid.json").read_text()
        )
        claims |= {
            "sub": f"run_trial{secrets.token_hex(8)}",
            "iat": now,
            "exp": now + 300,
            "tools": ["github.search_issues"],
            "require_approval": [],
        }
        public = self.kms.get_public_key(KeyId=KMS_ALIAS)["PublicKey"]
        from cryptography.hazmat.primitives.serialization import load_der_public_key

        numbers = load_der_public_key(public).public_numbers()
        jwk = {
            "crv": "P-256",
            "kty": "EC",
            "x": b64url(numbers.x.to_bytes(32, "big")),
            "y": b64url(numbers.y.to_bytes(32, "big")),
        }
        kid = b64url(
            hashlib.sha256(json.dumps(jwk, separators=(",", ":"), sort_keys=True).encode()).digest()
        )
        header = {"alg": "ES256", "kid": kid, "typ": "JWT"}
        signing_input = (
            f"{b64url(json.dumps(header).encode())}.{b64url(json.dumps(claims).encode())}"
        )
        der = self.kms.sign(
            KeyId=KMS_ALIAS,
            Message=hashlib.sha256(signing_input.encode()).digest(),
            MessageType="DIGEST",
            SigningAlgorithm="ECDSA_SHA_256",
        )["Signature"]
        r, s = decode_dss_signature(der)
        return f"{signing_input}.{b64url(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"

    def cleanup(self) -> None:
        print("Deleting what the trial made")
        if "function" in self.made:
            self.fn.delete_function(FunctionName=self.name)
        if "role" in self.made:
            self.iam.delete_role_policy(RoleName=self.role, PolicyName="kms")
            self.iam.detach_role_policy(
                RoleName=self.role,
                PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
            )
            self.iam.delete_role(RoleName=self.role)
        if "function" in self.made:
            # Lambda delivers the last log lines late and would recreate a group deleted at once.
            group = f"/aws/lambda/{self.name}"
            time.sleep(30)
            with contextlib.suppress(ClientError):
                self.logs.delete_log_group(logGroupName=group)
            time.sleep(15)
            if self.logs.describe_log_groups(logGroupNamePrefix=group)["logGroups"]:
                print(f"  log group {group} came back; delete it with")
                print(f"  aws logs delete-log-group --log-group-name {group}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--profile", default="default")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--keep", action="store_true", help="leave the function and role")
    args = parser.parse_args()
    if args.profile == "prod":
        sys.exit("This project never uses the prod profile.")
    if not ZIP.is_file():
        sys.exit(f"{ZIP} is missing: uv run python scripts/lambda_zip.py")

    trial = Trial(args.profile, args.region)
    results: dict = {}
    try:
        trial.create()
        health, report, wall = trial.invoke(event("GET", "/health"))
        assert health["statusCode"] == 200, health
        results["cold_health"] = {"wall_ms": round(wall), **report}
        print(f"  cold GET /health: {health['statusCode']}, {results['cold_health']}")

        refused, _, _ = trial.invoke(
            event("POST", "/mcp", {"content-type": "application/json"}, "{}")
        )
        assert refused["statusCode"] == 401, refused
        print("  POST /mcp without a token: 401")

        initialize = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "lambda-trial", "version": "0"},
            },
        }
        answer, report, wall = trial.invoke(
            event(
                "POST",
                "/mcp",
                {
                    "content-type": "application/json",
                    "accept": "application/json, text/event-stream",
                    "x-run-token": trial.run_token(),
                },
                json.dumps(initialize),
            )
        )
        assert answer["statusCode"] == 200, answer
        server = json.loads(answer["body"])["result"]["serverInfo"]
        results["mcp_initialize"] = {"wall_ms": round(wall), **report, "server": server}
        print(f"  POST /mcp initialize with a KMS-signed token: 200, server {server.get('name')}")

        warm = [trial.invoke(event("GET", "/health")) for _ in range(10)]
        durations = [r["Duration"] for _, r, _ in warm]
        walls = [w for _, _, w in warm]
        results["warm_health"] = {
            "duration_ms_median": statistics.median(durations),
            "wall_ms_median": round(statistics.median(walls)),
            "duration_ms_max": max(durations),
        }
        print(f"  warm GET /health x10: {results['warm_health']}")
        out = ROOT / "build" / "lambda-trial" / f"results-{time.strftime('%Y%m%d-%H%M%S')}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(results, indent=2))
        print(f"Results: {out}")
    finally:
        if not args.keep:
            trial.cleanup()
    return 0


if __name__ == "__main__":
    sys.exit(main())
