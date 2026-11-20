# loadtest

Python 3.12, uv. Load driver against the AWS dev deployment (M7): stub model, gateway's fake
connector, never real providers or GitHub. Design: `docs/architecture.md` → Testing (Load and
chaos) and Deployment (Load-test mode); ADR 0024. A client of the public API and a reader of
CloudWatch, ECS and Terraform outputs: it changes no AWS resource and touches no table.

## Commands

Run from `loadtest/`.

| What | Command |
| --- | --- |
| Install | `uv sync` |
| Test | `uv run pytest` (no AWS, no network) |
| Lint / format | `uv run ruff check . && uv run ruff format --check .` / `uv run ruff format .` |
| Load test | `uv run loadtest --runs 100 --rate 10` (needs load-test mode; costs money, see below) |

## A test, step by step

1. Estimate and get a go (100 runs: about $0.10-0.25, mostly Fargate tasks while they scale in).
2. `load_test = true` in `infra/envs/dev/terraform.tfvars`, `terraform -chdir=infra/envs/dev apply`
   (the user runs it). The driver refuses unless the outputs show `load_test = true` and the
   gateway on `fake` only.
3. `uv run loadtest --runs N --rate R`: creates and publishes `load-stub` (the contracts example
   spec, trigger `manual.load.run`, which no webhook sends), starts N runs R per second, polls
   them every `--poll` s (default 10) until final, then reads spans and metrics and prints the
   numbers. Exit 0 only if every run `SUCCEEDED`.
4. `load_test = false`, apply; `plan` must show "No changes". Workers return to 0 by themselves
   (about 5-8 minutes after the queue is empty).

## Numbers and where they come from

- Queue to start: the run's `created_at` (API) to the start of its first "run.attempt" span in
  `aws/spans`, matched by `trace_id`; one Logs Insights query, repeated until every succeeded run
  has its span (`--span-wait`, default 300 s). Spans arrive about 20-30 s after an attempt ends.
- Created to final: `created_at` to `updated_at` of succeeded runs.
- Runs per minute: final runs over first `created_at` to last final `updated_at` (includes the
  wait for workers); "once working": over first "run.attempt" start to last final `updated_at`.
- Redelivered: runs with more than one "run.attempt" span (0 expected without chaos).
- Workers: `describe_services` every 15 s (most running at once); CPU and memory: `AWS/ECS`
  `CPUUtilization` / `MemoryUtilization`, percent of the 0.5 vCPU / 1 GB task, one-minute
  points; CloudWatch publishes them a minute or two late, so the driver asks again every 30 s
  until the minute of the last final run is in (up to 5 minutes).
- Retried answers: 429 (throttle), 502/503/504 (Lambda limit, timeouts) by status; each request
  is tried 4 times with backoff.

## Layout

- `src/loadtest/cli.py`: arguments, wiring, report, results file.
- `driver.py`: `start_runs` (fixed rate, every POST leaves on schedule), `poll_until_final`.
- `api.py`: public API client (`X-Api-Key`), retries on 429/502/503/504.
- `aws.py`: Terraform outputs and the load-mode check, tenant key (SSM), Logs Insights query,
  ECS metrics and samples. Clients are passed in; tests use fakes.
- `stats.py`: percentiles, `RunRecord`, `summarize`.
- `spec.py`: the load agent's spec (name and trigger swapped in the example spec).

## Gotchas

- Cold or warm: right after a test the workers are still up (about 10 minutes until 0), so a
  second test measures warm workers. A cold test from an idle queue needs more than six hours
  without queue activity: SQS then stops sending metrics, and the scale-out alarm waits for them
  (about 6 minutes measured, up to 15 documented).
- The driver needs about 5 more minutes after the last run ends (spans, then metrics).
- A 100-run test costs about $0.10-0.25 (Fargate while it scales in); nothing calls a real model.

## Conventions

- Never print or save the tenant key; results (`results/<time>-<runs>.json`, git-ignored) hold
  run and trace ids, times and statuses only.
- AWS profile `default`; `prod` is refused.
- Do not publish or start `issue-triage` or `demo-stub` from here.
