# M7 load and chaos report

Runs on the dev deployment on AWS, 2026-11-20 and 2026-11-21 (UTC), with the stub model and the
gateway's fake connector. Driver: `loadtest/` (`AGENTS.md` there has the commands). Raw results
(per run: status, times, receive count) are in git-ignored `loadtest/results/`.

**Summary.** 1,000 runs started within 50 s all `SUCCEEDED`, with up to 918 executing at the same
time on 20 workers. With 12 of those workers stopped during a 1,000-run test (6 killed mid-run),
88 runs were taken over by other workers and finished: no run lost, no duplicate side effect. No
Lambda throttle or error and no retried API request in any test. Two things limit the platform
today: starting workers from 0 (about 3 minutes with an active queue, about 6 after a quiet
day), and a 0.5 vCPU worker's CPU at 50 runs at once with fast model calls.

## Setup

- **Deployment** (one region, `docs/architecture.md` → Deployment): API Gateway HTTP API; control
  plane on Lambda (Java 21, SnapStart, 1,769 MB arm64); tool gateway on Lambda (Python); workers
  on ECS Fargate (arm64, 0.5 vCPU, 1 GB), scaled from 0 on the run queue's depth (+1 for 1-9
  waiting, +5 for 10-49, +10 for 50 or more; 2-minute cooldown); SQS run queue; DynamoDB
  on-demand. Account Lambda concurrency 1,000.
- **Load-test mode** (ADR 0024, `load_test = true`): API throttle 1,000 requests per second per
  route (burst 2,000; 200/400 for the 100-run tests), up to 20 workers, gateway on the fake
  connector, stub model latency `MODEL_STUB_LATENCY_MS`, runs per worker `WORKER_CONCURRENCY` (4
  for the 100-run tests, 50 for the 1,000-run tests). Normal dev: 5 per second, 2 workers, 4 runs
  each, real GitHub.
- **Agent `load-stub`**: the contracts example spec under its own name and trigger. Each run: 3
  stub model calls (fixed latency each) and 2 tool calls through the gateway
  (`github.search_issues`, read-only; `github.add_label`, idempotent), then a final answer.
- **Driver**: starts N runs at a fixed rate (`POST /runs`), polls each until final, then reads
  span start times (`aws/spans`, one Logs Insights query), ECS CPU and memory, the gateway access
  log, and, with chaos, stops a random running worker task every S seconds (`ecs.stop_task`).

## Results

| # | Test | Latency, runs per worker | Workers at start | Succeeded | Queue to start p50 / p95 | Created to final p50 / p95 | Most executing at once | Workers (most) | Worker CPU avg / max |
| - | ---- | ------------------------ | ---------------- | --------- | ------------------------ | -------------------------- | ---------------------- | -------------- | -------------------- |
| 1 | 100 runs, 10/s | 2 s, 4 | 0, queue idle for hours | 100/100 | 377 s / 383 s | 384 s / 390 s | 40 | 10 | 10% / 32% (busy minute) |
| 2 | 100 runs, 10/s | 2 s, 4 | 10 running | 100/100 | 0.1 s / 1.2 s | 6.7 s / 7.8 s | at most 60 (15 x 4) | 15 | 3% / 15% |
| 3 | 100 runs, chaos every 20 s | 15 s, 4 | 0 | 100/100 | 275 s / 385 s | 319 s / 401 s | 40 | 10 | 4% / 44% |
| 4 | 1,000 runs, 20/s | 2 s, 50 | 0 | 1,000/1,000 | 172 s / 180 s | 186 s / 193 s | 249 | 9 | 19% / **100%** |
| 5 | 1,000 runs, 20/s | 15 s, 50 | 0 | 1,000/1,000 | 220 s / 238 s | 268 s / 284 s | 500 | 10 | 11% / 48% |
| 6 | 1,000 runs, 20/s | 15 s, 50 | 20 running | 1,000/1,000 | 0.0 s / 0.1 s | 45.6 s / 45.7 s | **918** | 20 | 9% / 38% |
| 7 | 1,000 runs, chaos every 10 s | 15 s, 50 | 20 running | 1,000/1,000 | 0.0 s / 80 s | 45.6 s / 95 s | 829 | 20 | 5% / 38% |

Throughput once workers were up (first run start to last final): test 2 347 runs per minute,
test 4 917, test 5 464, test 6 628, test 7 361. Memory stayed under 16% of 1 GB in every test.
Test 2's results file was not written (a driver bug, fixed); its numbers are from the driver's
printout. Test 6 also ran chaos, but the driver started chaos only after all runs were sent, so
its 2 stops came late and both workers finished their runs (fixed; test 7 is the chaos test).

Chaos (tests 3 and 7):

| # | Workers stopped | Killed mid-run (exit 137) | Finished runs, then exited (exit 0) | Runs taken over | Lost runs | Tool calls (`ok`) | Repeated | Duplicate side effects |
| - | --------------- | ------------------------- | ----------------------------------- | --------------- | --------- | ----------------- | -------- | ---------------------- |
| 3 | 8 | 5 | 3 | 13 | 0 | 200 | 0 | 0 |
| 7 | 12 | 6 | 6 | 88 | 0 | 2,001 | 1 (`github.add_label`) | 0 |

The repeat in test 7 is the designed path (ADR 0013): the worker was killed while its
`add_label` call was in flight (the gateway ran it 0.5 ms before the kill), so the result was
never stored; the worker that took the run over found the call's key claimed without a result
and repeated it because the tool is idempotent ("repeating github.add_label (outcome unknown,
safe to repeat)" in its log). The same label twice is one label. A tool that is not safe to
repeat would have failed the run instead.

Platform side, most at once in any test: control-plane Lambda 61 concurrent executions, gateway
Lambda 195 (test 4), API about 207 requests per second (test 4). 0 Lambda throttles, 0 Lambda
errors, 0 API 429s. One API 5xx in test 1, not on a driver request (the driver's requests were never retried);
every run succeeded.

## Limits found

- **Lambda concurrency 10** on a new account: a burst of 30 requests got 503s (M6). Raised to
  1,000 by a Service Quotas request (2026-11-20, approved the same day).
- **An idle queue delays scale-out** (test 1): SQS stops sending a queue's metrics after six hours
  with no call to it and resumes them up to 15 minutes after it wakes, so the scale-out alarm
  fired about 6 minutes after the runs were queued; the tasks then ran within 20 s. Fixed by an
  hourly `TagQueue` call that keeps the queue active (ADR 0025); the calls succeed
  (CloudTrail). Not yet confirmed over a quiet night (an optional follow-up: the queue's metric
  points should keep coming after six hours without runs).
- **Scale-out from 0 with an active queue takes about 3-4 minutes** (tests 3-5, queue to start
  p50 172-275 s; in test 3 the alarm fired 3.5 minutes after the runs were queued and the tasks
  ran 30 s later): CloudWatch's lag on SQS metrics plus the alarm's one-minute period. The first step also adds only 5 workers when
  the alarm sees the backlog while it is still growing, and the next step comes 2 minutes later:
  in test 4 five workers finished all 1,000 runs before the second step, so 249 was the most
  executing at once.
- **A 0.5 vCPU worker saturates at 50 runs at once with 2 s model calls** (test 4: CPU 100%,
  each run's working time 13.3 s against about 6.5 s ideal). With 15 s calls (tests 5-7) the same
  50 runs used at most 48%. Real model calls take seconds, so the right number per worker
  depends on the model; 4 (dev) leaves a worker mostly idle (15-44% at most).
- **ECS stops a task politely first**: SIGTERM, the worker finishes its runs, SIGKILL after 30 s.
  Runs shorter than that are never cut, so the chaos tests use 15 s model calls (runs of about
  45 s).
- **A killed worker loses the spans it had not uploaded** (batch exporter): 3 runs in test 3 and
  8 in test 7 have no "run.attempt" span, and counting spans misses every takeover. The driver
  counts takeovers from the run message's receive count instead (agrees with SQS: 100 sent,
  113 received in test 3).
- **Fargate quota 64 vCPU** (about 128 workers at 0.5 vCPU): not reached (20).

## What this does not show

- **Real model and tools**: the stub answers after a fixed wait and the fake connector at once,
  with no rate limits. A real provider would limit 1,000 runs at once long before the platform
  does (and cost money), and GitHub limits an App to about 5,000 requests an hour. Per-tenant
  limits on runs at once are not built yet.
- **A tool that is not safe to repeat under chaos**: the stub calls only a read-only and an
  idempotent tool. A kill during, say, a Slack post would fail that run by design (ADR 0013),
  not repeat it; that path is tested locally (`make chaos`, unit tests), not here.
- **Traffic shape**: 1,000 runs within 50 s, not a day of mixed traffic.

## Cost

Estimate: well under $1 for all seven tests (budget for M7: $5). Mostly Fargate (up to 20 tasks
at about $0.02 per task-hour, for the tests and the scale-in after each), plus Lambda and API
Gateway requests (about 75,000 in all) and CloudWatch logs; no model cost (stub). Actual, from
Cost Explorer on the `project` tag: _pending (M6 cost check)_.

## Reproduce

From the repo root, with the AWS profile `default` and the dev deployment applied:

1. `load_test = true` in `infra/envs/dev/terraform.tfvars` (for tests with 15 s calls, also a
   five-value `load_test_settings` block; see `infra/envs/dev/terraform.tfvars.example`), then
   `terraform -chdir=infra/envs/dev apply`.
2. `cd loadtest && uv run loadtest --runs 1000 --rate 20 --poll 15` (add `--chaos-every 10
   --chaos-max 12` for chaos).
3. `load_test = false` (remove the block), apply, and check that `plan` shows no changes.
