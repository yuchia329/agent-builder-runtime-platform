# 0024. Load-test mode as one Terraform switch; scale-out sized to the backlog

- Status: Proposed
- Date: 2026-11-20

## Context

M7 runs 100, then 1,000 stub-model runs against the dev deployment on AWS. Dev is also live
(M6): the gateway runs the `github` connector on the sandbox repo, and the GitHub App's webhook
starts `issue-triage` on every new issue. Normal settings protect that (ADR 0023): API throttle
5 per second per route, at most 2 workers. A load test needs different values for a short time:
a higher throttle (about 100 requests per second reach `/mcp` alone at 80 runs at once), more
workers, the gateway's fake connector (stub tool calls must never reach GitHub), and a stub model
that takes as long as a real one.

Scale-out added one worker per alarm with a 2-minute cooldown: reaching 20 workers would take
about 40 minutes, longer than the test.

`infra/scripts/aws_demo.py` published the stub spec as `issue-triage`, replacing the live agent,
and its trigger (`github.issue.opened`) would have made every real issue start a stub run.

Options looked at for the mode: editing each value in `terraform.tfvars` (four values to set and
four to restore by hand; a half-restored dev keeps a high throttle or a fake gateway); a second
environment (`envs/load/`: a second API, tables and queue, more to apply and pay for); one
boolean that switches a fixed set of values.

## Decision

- **One switch:** `load_test = true|false` (default false) in `terraform.tfvars`. `load_test.tf`
  computes the values from it: `load_test_settings` (throttle 200 per second, burst 400; 20
  workers; stub latency 2,000 ms) when on, the normal variables when off. The gateway runs
  `fake` (no `GITHUB_REPOS`) and the worker gets `MODEL_STUB_LATENCY_MS` only when on. Outputs
  `load_test` and `gateway_connectors` show the mode.
- **Scale-out sized to the backlog, in both modes:** step scaling on the queue's visible messages:
  1 to 9 adds 1 task, 10 to 49 adds 5, 50 or more adds 10 (2-minute cooldown, up to the limit).
  One run still adds exactly one task. Scale-in is unchanged.
- **`aws_demo.py` has its own agent:** it publishes the example spec as `demo-stub` with the
  trigger `manual.demo.run` (no webhook sends it) and refuses unless the gateway runs `fake`.

## Consequences

- One line starts and ends a test; `plan` after `load_test = false` shows the normal values, and
  "No changes" after applying confirms dev is back.
- While the mode is on, the live `issue-triage` still answers real issues with the real model
  but fake tools: about $0.01 a run, nothing written to GitHub. Tests are short, and the mode is
  turned off right after.
- Load values live in one variable; changing them for a test is a tfvars edit, not code.
- Measured (2026-11-20): in load mode `aws_demo.py` succeeded with `demo-stub` (each stub model
  call 2,000 ms); `issue-triage` stayed at version 4.
- Large tests still need the account's Lambda concurrency above 10 (increase requested); until
  then the throttle in load mode is above what Lambda can serve, and excess requests get 503.

## Amendment (2026-11-20): runs per worker and a higher throttle

At 20 workers of 4 runs each, at most 80 runs executed at once, so a 1,000-run test only measured
a queue being cleared; worker CPU stayed at 15-44% (stub runs mostly wait). Load mode now also
sets the runs each worker executes at once: `worker_concurrency` (normal variable, default 4;
`load_test_settings.worker_concurrency`, default 50, so 20 x 50 = 1,000 executing). The load
throttle rises to 1,000 per second, burst 2,000 (from 200/400): at 1,000 runs at once `/mcp`
needs about 400 requests per second and `/internal` more, far below API Gateway's account limit
(10,000) and within Lambda's 1,000 concurrency at about 0.1 s a request. Output
`worker_concurrency` shows the value in use. A temporary `load_test_settings` block in tfvars
must list all five values. Whether a 0.5 vCPU task can run 50 at once is what the test finds out.
