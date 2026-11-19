# evals

Python 3.12, uv. Agent evals: runs a spec's tasks on its real model against simulated services and
checks the agent's decisions (not the platform's correctness). Design: `docs/architecture.md` →
Testing; ADR 0016 (evals run the worker's code in process). Task format: `task.schema.json`.

## Commands

Run from `evals/`.

| What | Command |
| --- | --- |
| Install | `uv sync` (and `cd ../tool-gateway && uv sync` once: tests and runs start the real gateway) |
| Test | `uv run pytest` (no Docker, no model provider: `ScriptedModel`) |
| Run tasks on the real model | `set -a && . ../.env && set +a && uv run evals run tasks/issue-triage/<file>.yaml` (needs the `MODEL_*` settings; about $0.005 per issue-triage task) |
| Lint | `uv run ruff check . && uv run ruff format --check .` |
| Format | `uv run ruff format .` |
| Gateway did not start | the error names its log file (a temporary folder); usually `../tool-gateway` lacks `uv sync` |

## Layout

- `task.schema.json` + `examples/` (`task.valid.yaml`, `task.invalid.yaml`: one case per entry).
- `tasks/<spec name>/*.yaml`: one task per file; `spec` must equal the folder name.
- `src/evals/task.py`: loads a task and its spec (`../specs/<name>.yaml`, checked against
  `contracts/agent-spec.schema.json`); load-time checks the schema cannot express.
- `runner.py`: `Evaluation` (context manager: simulator, internal run endpoint, gateway, started
  once) and `run(task, model)`: `handle_run` under a lease with in-memory checkpoints (so a run
  continues after its approval), a new run token per attempt, every approval request approved
  (`eval:auto`), `RecordingTools` around `GatewayTools`.
- `services.py`: `RunTokens` (signs like the control plane), `InternalRunServer`
  (`GET /internal/runs/{id}` for the gateway's approval check), `GatewayProcess`.
- `checks.py`: one function per check kind over an `Outcome`.
- `simulators/github.py`: the one repository `octocat/Hello-World`; search semantics in its
  docstring.
- `cli.py`: `evals run`; `model_from_env` builds the worker's model router.

## Conventions

- The gateway's environment is built from scratch (`GatewayProcess._env`): never pass `.env`
  values to it. Its GitHub App key and run-token keys are throwaway, made per process.
- A new check kind: schema `$defs/check`, a function in `checks.py`, a good and a bad case in
  `tests/test_checks.py`, and a load-time check in `task.py` if it names tools or patterns.
- A new connector's simulator: a module in `simulators/`, its world under `world.<connector>` in
  the schema, `CONNECTOR_<NAME>_BASE_URL` in `GatewayProcess._env`, its end state in
  `Outcome.world`.
- The simulator answers only what the connector sends; anything else is 404 like the real API.
  Response shapes follow the cleaned recordings in `../tool-gateway/tests/fixtures/`.
- Tests never call a model provider; only `evals run` does, and it spends money.
- Tasks are made up: no real people, repositories or issue text. The repository is always
  `octocat/Hello-World`.
