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
| Suite (from repo root) | `make eval` (every spec with tasks) or `make eval SPEC=issue-triage`: 3 runs per task, 4 at once, report, comparison with `baselines/<spec>.json`; exit 1 on a regression. About $0.35 and 10 minutes for issue-triage |
| Save a new baseline | `make eval-baseline SPEC=<name>`, then commit `evals/baselines/<name>.json` (only after reading the report) |
| Suite with a modified spec | `uv run evals suite <spec> --specs-dir <folder with <spec>.yaml>` (after loading `.env`); tries a prompt change without editing `specs/` |
| Why did a run fail | the report lists each failed run's checks and tool calls; everything (outputs too) is in `results/<spec>-<time>.json` (git-ignored) |
| Suite without the judge | `uv run evals suite <spec> --no-judge` (after loading `.env`) |
| Make a labels file for a judge | `uv run evals labels <spec>`: about 50 answers from saved `results/` into `labels/<spec>.yaml`; refuses to replace an existing file without `--force` |
| Check the judge against the labels | `set -a && . ../.env && set +a && uv run evals judge-check <spec>` (about $0.05 and a minute for 75 labels): prints disagreements and agreement, writes `judges/<spec>.json`. Run it twice: the judge varies a little between runs |
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
- `suite.py`: `run_suite` (runs x tasks over `parallel` `Evaluation`s, one per worker thread, so
  runs never share a simulator), `compare` with a baseline, `report`, baseline and results files.
- `baselines/<spec>.json`: committed; per task passes out of runs, model, spec hash, cost.
- `judge.py`: `Judge` (rubric, context and answer in; `{"verdict", "why"}` out, through the worker's
  model adapter), `context_of` (what the judge sees), `grade_labeled`, `calibration`, `trusted`.
- `labels.py`: `sample` (answers from saved results: runs of the current spec plus runs of other
  spec versions, so bad answers are included), `read`, `write`.
- `rubrics/<spec>.md` (the criteria, plain words), `labels/<spec>.yaml` (answers labeled good or
  bad), `judges/<spec>.json` (last agreement check): all committed. A spec without a rubric has
  no judge.
- `cli.py`: `evals run` (one run per task), `evals suite`, `evals labels`, `evals judge-check`;
  `model_from_env` builds the worker's model router.

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
- Regression rules (`suite.py` docstring): a task regresses when its pass fraction drops by at least
  2/3; the suite when the run pass rate drops more than 5 points. Smaller moves are noise
  (an unchanged spec moved one task from 0/3 to 3/3), so do not tighten them without data.
- Suite tests use `StubModel` (stateless, safe across threads), not `ScriptedModel`.
- The judge counts in a suite (a "bad" verdict fails the run) only while `judges/<spec>.json` says
  trusted for the current rubric text and judge model: at least 90% agreement with the labels, 5
  or more bad labels, 80% of them caught. Any rubric edit makes it "reported only" until
  `judge-check` runs again. Once it counts, save a new baseline (`make eval-baseline`), or the
  comparison mixes judged and unjudged runs.
- The judge grades meaning; format belongs to the `output` checks. It sees what the agent could
  see (new issue, repository labels, each existing issue's title, state and labels), not the
  agent's tool calls, and the rubric tells it not to grade statements about the agent's own
  searches and actions. A fact the agent can see but `context_of` does not give gets called
  "invented": add it to `context_of` and refresh the labels file's stored context.
- `labels/issue-triage.yaml` was labeled by Claude (Fable 5.2) at the user's request, not by a
  person (see its header); the user may change any label. Items 1-50 were used to adjust the
  rubric; items 51-75 were labeled after it was frozen. To adjust the rubric again, validate on
  newly sampled labels, not on answers already used for adjusting.
- Tests never call a model provider; only `evals run` and `evals suite` do, and they spend money.
- Tasks are made up: no real people, repositories or issue text. The repository is always
  `octocat/Hello-World`.
