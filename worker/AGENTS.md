# worker

Python 3.12, uv. Executes runs: the agent loop, budgets, approvals. Design: `docs/architecture.md`
→ Worker, ADR 0005 (compute-agnostic core), ADR 0009 (the worker owns the agent loop), ADR 0010
(provider-neutral model layer).

## Commands

Run from `worker/`.

| What | Command |
| --- | --- |
| Install | `uv sync` |
| Test | `uv run pytest` (Docker must run: SQS and DynamoDB tests start ElasticMQ and DynamoDB Local with Testcontainers) |
| Test, no Docker | `uv run pytest -m "not docker"` (MCP tests start the real gateway: needs `cd ../tool-gateway && uv sync` once) |
| Tests hang at 0% CPU | iCloud evicted `.venv` files (repo under `~/Desktop`): `find .venv -flags +dataless \| wc -l`; if not 0, `rm -rf .venv && uv sync` |
| Run against the local stack | `set -a && . ../.env && set +a && uv run worker` (needs `make stack` and the control plane on the host) |
| Real GitHub tools locally | In `../.env`: `TOOLS_PROVIDER=local`, `GITHUB_TOKEN` (fine-grained, sandbox repo only, Issues read/write), `GITHUB_REPOS=owner/repo` |
| Tools through the gateway | In `../.env`: `TOOLS_PROVIDER=mcp`, `TOOL_GATEWAY_URL=http://localhost:8090`; run the gateway (`tool-gateway/AGENTS.md`) and give the control plane `RUN_TOKEN_*` so runs carry a token |
| Real model locally | In `../.env`: `MODEL_PROVIDERS=minimax` and the `MODEL_MINIMAX_*` lines from `../.env.example` with your key; publish a spec with `model: minimax/MiniMax-M3` |
| Real Bedrock locally | In `../.env`: add `bedrock` to `MODEL_PROVIDERS` and the `MODEL_BEDROCK_*` lines from `../.env.example` (profile from `~/.aws`, no keys in `.env`); run the worker on the host (the image has no `~/.aws`); spec `model: bedrock/amazon.nova-lite-v1:0` |
| See spans locally | `TRACING_EXPORTER=logging` (each span printed as JSON on stdout) |
| Slow stub model (load tests) | `MODEL_STUB_LATENCY_MS=2000` (each `stub/*` call waits 2 s; 0 to 60000, default 0; on AWS through `worker_model_env` in tfvars) |
| See metric lines locally | `METRICS_NAMESPACE=AgentPlatform/local` (one `ModelCostUsd` JSON line on stdout per model call) |
| Metric on AWS (after a run) | `aws cloudwatch get-metric-statistics --namespace AgentPlatform/dev --metric-name ModelCostUsd --statistics Sum SampleCount --period 3600 --start-time <ISO> --end-time <ISO> --profile default` |
| Live X-Ray check (dev AWS, fraction of a cent) | `LIVE_AWS=1 uv run pytest tests/unit/test_live_xray.py -s` (profile `default`; one span through the OTLP endpoint, found in `aws/spans` in about 20-30 s; needs Transaction Search on) |
| Lint | `uv run ruff check . && uv run ruff format --check .` |
| Format | `uv run ruff format .` |
| Image (from repo root) | `docker build -f worker/Dockerfile .` (or `make up`) |
| Fargate image (from repo root) | `make worker-image` (linux/arm64, tag `agent-platform-worker:<commit>`) |
| Push to ECR (from repo root; in the infra compute task) | `url=$(terraform -chdir=infra/envs/dev output -raw worker_repository_url)`, `aws ecr get-login-password --profile default \| docker login --username AWS --password-stdin ${url%%/*}`, `docker tag agent-platform-worker:<commit> $url:<commit>`, `docker push $url:<commit>` (tags are immutable: a new commit, a new tag) |

## Layout

- `src/worker/core/`: the loop (`handle_run.py`), budget, model table (`models.py`), context trimming
  (`trim.py`), types, errors. Imports only the standard
  library, `worker.core` / `worker.ports`, and `jsonschema`. `tests/unit/test_core_boundary.py`
  fails on any other import.
- `src/worker/ports/`: `Protocol` interfaces only (`ControlPlane`, `Model`, `ToolClient`, `Clock`,
  `RunQueue`, `RunStateStore`, `Tracer`, `Metrics`).
- `src/worker/adapters/`: implementations. Vendor SDKs (boto3, httpx, Strands) live only here.
  Models: `model_router.py` (picks the provider from `<provider>/<model id>`),
  `model_openai_compat.py` (OpenAI Chat Completions format: MiniMax, OpenAI, OpenRouter, vLLM,
  ...), `model_bedrock.py` (Bedrock Converse via boto3), `model_stub.py` (built-in provider
  `stub`).
- Run state: `run_state_memory.py`, `run_state_dynamodb.py` (item layout in its docstring),
  `run_state_payload.py` (shared JSON and size check). Table keys only in
  `worker/dynamodb/run-state-table.json` (TTL in `run-state-ttl.json`); tests and the local
  stack create the table from these files, never from code.
- `src/worker/entrypoints/poll_loop.py`: the Fargate entrypoint (queue → lease → `handle_run`,
  heartbeat renewing message and lease, release, delete-or-keep). `config.py` reads env vars; `wiring.py` is the only module that picks adapters.
- `tests/contract/`: one suite per port, parametrized over adapters. A new adapter is added to the
  suite's `ADAPTERS` and must pass it.
- `tests/integration/`: `handle_run` end to end with in-memory adapters.
- `tests/contracts.py`: helpers that read `../contracts/` (example run, schema validators).
- `tests/fake_openai_server.py` + `tests/fixtures/<provider>/*.json`: replays recorded provider
  replies for the OpenAI-compatible adapter.
- `tests/fake_bedrock.py` + `tests/fixtures/bedrock/*.json`: botocore `Stubber` replaying
  recorded Converse replies (`ResponseMetadata` removed) for the Bedrock adapter.
- `tests/fake_github_server.py` + `tests/fixtures/github/*.json`: answers like the GitHub REST
  API (routes registered with `on`, `fail_next` for retries) for `tools_local.py`.
- `adapters/tools_mcp.py` (`GatewayTools`): tools through the gateway over MCP (SDK client).
- `adapters/tracing_otel.py`: `OtelTracer`, `build_tracer` (`TRACING_EXPORTER`), the SigV4-signed
  X-Ray OTLP exporter; `core/tracing.py`: `NoopTracer`, the `Deps` default.
- `adapters/metrics_emf.py`: `EmfMetrics`, one CloudWatch embedded metric format line per value
  on stdout (`METRICS_NAMESPACE`; ADR 0023); `core/metrics.py`: `NoopMetrics`, the `Deps` default.
- `tests/live_gateway.py`: the real tool gateway as a subprocess (fake connector, minimal env,
  throwaway P-256 key) and `RunTokens` signing run tokens like the control plane; session
  fixtures `live_gateway` and `run_tokens` in `conftest.py`.
- `tests/fake_control_plane_server.py`: local HTTP server for the internal API (state from
  `InMemoryControlPlane`, PATCH bodies checked against `RunUpdate`, SigV4 header required,
  `fail_next` for retry tests).

## Conventions

- Shapes come from `contracts/`. Tests use `contracts/examples/internal-run.valid.json` as the run
  and check every update against the contract's `RunUpdate` schema; never copy a contract shape
  into a test.
- A step is one model turn or one tool call. Check the budget before a step, record it after.
- Every model reply is checked in the loop before anything acts on it; add new safeguards there,
  with an integration test.
- boto3 reads `AWS_DEFAULT_REGION`, not `AWS_REGION`: pass `config.aws_region` to every boto3
  client explicitly.
- Errors crossing a port are the types in `core/errors.py`; adapters translate vendor errors.
  Messages in `ToolError` go to the model, so they must not contain secrets.
- A new model: add its price and context window to `core/models.py` from the provider's
  official pages (unlisted models fail before step 1). Tests that need another model pass
  `Deps(..., models={...})` instead of editing the table.
- Retries live in the adapter, not core: transient errors raise the adapter's `_Retryable`,
  everything else `ModelError`. Inject `sleep` in tests so retries never wait.
- Trimming (`core/trim.py`) must keep the conversation valid for every protocol: first message
  is the user input, each tool result follows its call, user/assistant alternate. Its tests
  check that shape. A new provider on a supported protocol is config only.
- A new provider's quirks: record one tool turn and one final turn (contract-suite inputs) into
  `tests/fixtures/<provider>/`, add it to the `Model` contract suite and the adapter unit tests.
  Recordings hold response bodies only, never request headers. Tests never call real providers.
- What a provider must see again in later turns goes in `ModelTurn.provider_data`; core copies
  it into the history and never reads it. Keep it JSON-serializable (checkpoints, M2).
- Bedrock tool names: `.` becomes `_` on the wire, never `-` (Nova rejects it); map replies back
  through the request's tool list, not by string replace.
- JSON values in `.env` (`MODEL_<NAME>_EXTRA_BODY`) need single quotes, or `sh` strips the
  inner quotes.
- Run-state writes other than `take_lease` must check the caller's live lease in the same
  DynamoDB transaction (fencing, ADR 0011); a new write method follows the same rule and gets a
  contract test with a stale worker. Lease expiry uses `Clock.time()`, never `monotonic()`.
- `STATE_BACKEND=memory` keeps leases in one process: fine for tests and a single host worker,
  no protection between workers. The local stack uses `dynamodb`.
- Handlers given to `PollLoop` take `(run_id, deps, attempt)` like `handle_run`; core checks
  `attempt.cancelled()` before every step, next to the budget, and signs checkpoints with
  `attempt.owner`. Without an `Attempt` or `Deps.state`, nothing is saved.
- Checkpoints hold what a step added (`Message.to_json`); never mutate the history in place
  (trim a copy), or saved steps stop matching it. A new message field needs `to_json` /
  `from_json` and the round-trip test in `tests/unit/test_types.py`.
- Resume tests (`tests/integration/test_resume.py`) kill a worker with `Killed`, a
  `BaseException` that `handle_run` does not catch, at a checkpoint write or a status report.
- DynamoDB expressions: put attribute names through `#` placeholders (`owner`, `ttl` are reserved)
  and pass only the names the expression uses, or DynamoDB rejects the call.
- Tests that need Docker get `@pytest.mark.docker` (or `marks=` on a `pytest.param`).
- `ScriptedModel` (tests: one script, records calls) vs `StubModel` (running worker: stateless,
  picks the turn by how many assistant replies the conversation has, safe across concurrent runs;
  `latency_seconds` waits before each reply, with an injectable `sleep` that tests pass).
- A `require_approval` call runs only on a matching approved `RunRecord.approval` (step, tool,
  canonical-JSON arguments); otherwise the loop pauses again. Tests decide with
  `InMemoryControlPlane.decide()`, never by editing the run by hand, except to test mismatches.
- Every tool declares `read_only` / `idempotent` in its `ToolSpec` (ADR 0013). Set them only if
  true: a repeat after a crash relies on them. No flag means a crash mid-call fails the run.
- `tools_local.py` holds a real token until the M4 gateway: keep its guardrails (repo
  allowlist, search scoped to the repo, no issue bodies, existing labels only; `close_issue`
  reads the issue first, refuses pull requests, never re-closes, links `duplicate_of` by issue
  id in the same repo, posts no comment) and add the same kind for any new tool. GitHub write
  calls are often not idempotent (a re-PATCH overwrites `state_reason`): record a repeat
  before marking a tool `idempotent`. Tool results and errors go to the model; never include the token.
- Recorded GitHub fixtures are cleaned: owner/repo become `octocat/Hello-World`; users, avatars,
  node ids and bodies are removed. Check with `grep -ri <your-login> tests/fixtures` before
  committing a new recording.
- Tool adapters get a `ToolContext` on every `describe` / `call`; only `tools_mcp.py` uses it.
  Never log the run token: `RunRecord` and `ToolContext` reprs leave it out.
- The run token goes in `X-Run-Token` (contract). `Authorization` belongs to SigV4 when
  `TOOL_GATEWAY_SIGV4=true` (`SignedAuth`, which signs `host`, `content-type`, `x-run-token` and the
  body with `control_plane_http`'s signer); unsigned, the token also goes as `Authorization:
  Bearer` until every gateway reads `X-Run-Token` (roadmap, Cross-service changes).
- On Fargate, model API keys come from SSM through the task definition's `secrets` as
  `MODEL_<NAME>_API_KEY`: no code reads SSM. AWS credentials come from the task role (boto3's
  default chain); never put keys in the task definition.
- `GatewayTools` maps every failure to `ToolError` (the MCP SDK wraps errors in exception
  groups; unwrap with `_first`) and does not retry: repeats are core's decision (ADR 0013).
- Tracing (ADR 0022): the run's trace comes from the SQS attribute `traceparent`
  (`QueueMessage.trace_parent`), never the body. Span attributes are counts, names and ids only:
  never input, output, tool arguments, results, tokens or exception messages (`OtelTracer` records
  only the exception type; `tests/integration/test_tracing.py` checks for leaks). Adapters that
  call another service take `trace_headers(tracer)` and add `traceparent`; `GatewayTools` reads it
  on the caller's thread, before its event loop.
- Tracing tests use `build_tracer("none", ..., span_exporter=InMemorySpanExporter())`; the
  exporter drops spans after `shutdown()`, so read them before.
- Metrics (ADR 0023): `Deps.metrics.model_cost(cost)` after every model call, 0 included. The EMF
  line carries the value only: no dimensions, run ids, model names or anything from the run
  (each dimension combination is a billed metric). It goes to stdout, logs to stderr; keep it
  that way so a metric line is never part of a log record. `config.py` owns the namespace rule.
- The fake tools and scripted model are shared test infrastructure (later: chaos and load tests);
  keep them deterministic.
