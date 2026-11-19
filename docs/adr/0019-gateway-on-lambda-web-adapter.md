# 0019. Tool gateway on Lambda through the Lambda Web Adapter

- Status: Proposed
- Date: 2026-11-19

## Context

ADR 0007 puts the tool gateway on AWS Lambda behind the shared HTTP API, "served through a Lambda
adapter", without choosing one. The gateway is a Starlette app: the MCP Python SDK (2.x) in
stateless Streamable HTTP mode with JSON responses, the run-token check as ASGI middleware, and the
owner-login routes. Locally uvicorn serves it.

The MCP SDK runs requests inside a task group that its session manager starts in the app's
lifespan, and `StreamableHTTPSessionManager.run()` may be called only once per instance (checked in
SDK 2.3). Options:

- **Mangum** (ASGI adapter inside the Python handler): one process, no layer. But it runs the
  app's lifespan around every invocation, so the session manager would be started a second time
  and fail; working around it means owning a lifespan shim against SDK internals.
- **AWS Lambda Web Adapter (LWA)**: a Lambda extension, published by AWS as a layer, that starts
  the unchanged server once per execution environment, waits for a readiness path, and turns
  each API Gateway event into an HTTP request to it. The lifespan runs once, as locally.
- **A container image** with LWA copied in: same behavior, but needs an ECR repository and image
  builds for a service that has no native code.

## Decision

- The gateway runs on Lambda as a **zip package with the LWA layer**
  (`LambdaAdapterLayerArm64`, version pinned in Terraform), runtime `python3.12`, arm64.
  Handler `run.sh` starts `python3 -m gateway` on 127.0.0.1:8080;
  `AWS_LAMBDA_EXEC_WRAPPER=/opt/bootstrap`, `AWS_LWA_READINESS_CHECK_PATH=/health`.
- `scripts/lambda_zip.py` builds the package from the lock file with Linux arm64 wheels only
  (manylinux 2.34, Amazon Linux 2023), plus the `gateway` package, `connectors/` and the contract
  schemas: about 28 MB zipped, 56 MB unzipped.
- No SnapStart: the gateway's caller is the worker on Fargate, which itself takes a minute to
  start, and SnapStart on Python bills cache and restore. Startup may call AWS (KMS public key,
  SSM secrets; ADR 0018).

### Measured (2026-11-19, us-east-1, 1024 MB, `scripts/lambda_trial.py`)

Cold `GET /health`: 3.4 s of init (Python, imports, uvicorn, `kms:GetPublicKey`), 3.8 s on the
client's clock. `POST /mcp` without a token: 401. `POST /mcp` `initialize` with a token signed by the
dev KMS key: 200 in 17 ms. Warm `GET /health`: about 2 ms in the function, about 90 ms on the
client's clock. Memory used: 152 MB.

## Consequences

- The same app and the same server run locally and on Lambda; the MCP SDK's lifespan needs no
  shim. Tests keep using the real server (`LiveGateway`).
- The layer is an AWS-published dependency: its version is pinned and upgraded deliberately.
- A cold environment costs about 3.5 s once; tool calls after that are fast. If the worker ever
  calls the gateway on a user's critical path, provisioned concurrency or a lighter startup are the
  options.
- API Gateway caps a request at 30 s: a tool call (with its outside API's retries) must finish
  within that, though the worker waits up to 60 s.
- Memory can drop well below 1024 MB (152 MB used); the size is a Terraform setting.
