# worker

Python 3.12, uv. Executes runs: the agent loop, budgets, approvals. Design: `docs/architecture.md`
→ Worker, ADR 0005 (compute-agnostic core), ADR 0009 (the worker owns the agent loop).

## Commands

Run from `worker/`.

| What | Command |
| --- | --- |
| Install | `uv sync` |
| Test | `uv run pytest` (Docker must run: SQS tests start ElasticMQ with Testcontainers) |
| Test, no Docker | `uv run pytest -m "not docker"` |
| Run against the local stack | `set -a && . ../.env && set +a && uv run worker` (needs `make stack` and the control plane on the host) |
| Lint | `uv run ruff check . && uv run ruff format --check .` |
| Format | `uv run ruff format .` |
| Image (from repo root) | `docker build -f worker/Dockerfile .` (or `make up`) |

## Layout

- `src/worker/core/`: the loop (`handle_run.py`), budget, types, errors. Imports only the standard
  library, `worker.core` / `worker.ports`, and `jsonschema`. `tests/unit/test_core_boundary.py`
  fails on any other import.
- `src/worker/ports/`: `Protocol` interfaces only (`ControlPlane`, `Model`, `ToolClient`, `Clock`).
- `src/worker/adapters/`: implementations. Vendor SDKs (boto3, httpx, Strands) live only here.
- `src/worker/entrypoints/poll_loop.py`: the Fargate entrypoint (queue → `handle_run`, heartbeat,
  delete-or-keep). `config.py` reads env vars; `wiring.py` is the only module that picks adapters.
- `tests/contract/`: one suite per port, parametrized over adapters. A new adapter is added to the
  suite's `ADAPTERS` and must pass it.
- `tests/integration/`: `handle_run` end to end with in-memory adapters.
- `tests/contracts.py`: helpers that read `../contracts/` (example run, schema validators).
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
- Tests that need Docker get `@pytest.mark.docker` (or `marks=` on a `pytest.param`).
- `ScriptedModel` (tests: one script, records calls) vs `StubModel` (running worker: stateless,
  picks the turn by how many assistant replies the conversation has, safe across concurrent runs).
- The fake tools and scripted model are shared test infrastructure (later: chaos and load tests);
  keep them deterministic.
