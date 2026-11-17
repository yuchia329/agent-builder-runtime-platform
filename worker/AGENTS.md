# worker

Python 3.12, uv. Executes runs: the agent loop, budgets, approvals. Design: `docs/architecture.md`
→ Worker, ADR 0005 (compute-agnostic core), ADR 0009 (the worker owns the agent loop), ADR 0010
(provider-neutral model layer).

## Commands

Run from `worker/`.

| What | Command |
| --- | --- |
| Install | `uv sync` |
| Test | `uv run pytest` (Docker must run: SQS tests start ElasticMQ with Testcontainers) |
| Test, no Docker | `uv run pytest -m "not docker"` |
| Run against the local stack | `set -a && . ../.env && set +a && uv run worker` (needs `make stack` and the control plane on the host) |
| Real GitHub tools locally | In `../.env`: `TOOLS_PROVIDER=local`, `GITHUB_TOKEN` (fine-grained, sandbox repo only, Issues read/write), `GITHUB_REPOS=owner/repo` |
| Real model locally | In `../.env`: `MODEL_PROVIDERS=minimax` and the `MODEL_MINIMAX_*` lines from `../.env.example` with your key; publish a spec with `model: minimax/MiniMax-M3` |
| Real Bedrock locally | In `../.env`: add `bedrock` to `MODEL_PROVIDERS` and the `MODEL_BEDROCK_*` lines from `../.env.example` (profile from `~/.aws`, no keys in `.env`); run the worker on the host (the image has no `~/.aws`); spec `model: bedrock/amazon.nova-lite-v1:0` |
| Lint | `uv run ruff check . && uv run ruff format --check .` |
| Format | `uv run ruff format .` |
| Image (from repo root) | `docker build -f worker/Dockerfile .` (or `make up`) |

## Layout

- `src/worker/core/`: the loop (`handle_run.py`), budget, model table (`models.py`), context trimming
  (`trim.py`), types, errors. Imports only the standard
  library, `worker.core` / `worker.ports`, and `jsonschema`. `tests/unit/test_core_boundary.py`
  fails on any other import.
- `src/worker/ports/`: `Protocol` interfaces only (`ControlPlane`, `Model`, `ToolClient`, `Clock`).
- `src/worker/adapters/`: implementations. Vendor SDKs (boto3, httpx, Strands) live only here.
  Models: `model_router.py` (picks the provider from `<provider>/<model id>`),
  `model_openai_compat.py` (OpenAI Chat Completions format: MiniMax, OpenAI, OpenRouter, vLLM,
  ...), `model_bedrock.py` (Bedrock Converse via boto3), `model_stub.py` (built-in provider
  `stub`).
- `src/worker/entrypoints/poll_loop.py`: the Fargate entrypoint (queue → `handle_run`, heartbeat,
  delete-or-keep). `config.py` reads env vars; `wiring.py` is the only module that picks adapters.
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
- Tests that need Docker get `@pytest.mark.docker` (or `marks=` on a `pytest.param`).
- `ScriptedModel` (tests: one script, records calls) vs `StubModel` (running worker: stateless,
  picks the turn by how many assistant replies the conversation has, safe across concurrent runs).
- `tools_local.py` holds a real token until the M4 gateway: keep its guardrails (repo
  allowlist, search scoped to the repo, no issue bodies, existing labels only) and add the same
  kind for any new tool. Tool results and errors go to the model; never include the token.
- Recorded GitHub fixtures are cleaned: owner/repo become `octocat/Hello-World`; users, avatars,
  node ids and bodies are removed. Check with `grep -ri <your-login> tests/fixtures` before
  committing a new recording.
- The fake tools and scripted model are shared test infrastructure (later: chaos and load tests);
  keep them deterministic.
