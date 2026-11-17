# Roadmap

Current status, milestones and next tasks. Update this file as work moves; the other docs describe
the target design and should change only when the design does.

**Status:** M1 done (MVP): `specs/issue-triage.yaml` runs on real MiniMax (`minimax/MiniMax-M3`)
with real GitHub tools (`TOOLS_PROVIDER=local`) through the local stack, labels a new issue and
flags a planted duplicate (steps in `specs/README.md`). M0 `make up && make demo` still runs the
stub spec. Opening an issue in the sandbox starts a run by itself (`POST /triggers/github`, signed
webhook delivered locally through smee.io). Bedrock also works (`bedrock/amazon.nova-lite-v1:0`
through the Converse adapter), though Nova Lite triages worse than MiniMax. M2 (crash safety) in
progress: worker side done (lease, checkpoints, idempotent tool calls; ADR 0011-0013); left:
control-plane sweep and webhook dedupe, then `make chaos`.

## Approach

Build a thin slice through every service first, then deepen each part. Do not finish one service
before starting the next. Each milestone ends with something demoable.

## Where to start

Work the task lists below top to bottom: the next session takes the first unchecked task. The
order follows dependencies, so skipping ahead means building on something that does not exist yet.

M0 dependencies:

```
1 [contracts]      schemas every service validates against        needs nothing
2 [control-plane]  publish agents and versions; owns table design  needs 1
3 [local-stack]    DynamoDB Local + ElasticMQ                      needs 2 (table definition)
4 [contracts]      internal API contract (worker <-> control plane) needs nothing
5 [control-plane]  create runs, enqueue, internal API              needs 2, 3, 4
6 [worker]         handle_run with in-memory adapters              needs 1
7 [worker]         SQS, control-plane HTTP, poll loop              needs 3, 5, 6
8 [local-stack]    services in compose, make demo                  needs all
```

The control plane comes before the local stack because it owns the control-plane table's design;
its tests start their own DynamoDB Local with Testcontainers. Task 4 can be done any time, and 6
can run alongside 2 to 5. Start a session with
`/start <component>`; it picks the next task for that component.

## Milestones

| #   | Milestone      | Done when                                                                                                                                                     | Status      |
| --- | -------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------- |
| M0  | Local skeleton | `docker compose up`; publish a spec; one run completes end to end with the stub model                                                                         | Done        |
| M1  | Real agent     | Issue-triage agent runs on a real model (MiniMax) and calls GitHub directly from the worker (personal token)                                                  | Done        |
| M2  | Crash safety   | Lease, checkpoints, idempotency; killing a worker mid-run causes no duplicate tool call                                                                       | In progress |
| M3  | Approvals      | Close-issue pauses; Slack buttons resume or reject the run                                                                                                    | Not started |
| M4  | Gateway        | Tool calls move into the gateway; OpenAPI → MCP generator; OAuth and team bots                                                                                | Not started |
| M5  | Evals          | Suite runs in GitHub Actions and blocks a PR that lowers success                                                                                              | Not started |
| M6  | AWS deploy     | Terraform deploys everything scale-to-zero (ADR 0007): Lambdas behind API Gateway on a custom domain, workers at 0; traces in X-Ray; no hourly cost when idle | Not started |
| M7  | Scale proof    | 1,000 concurrent runs on the stub model; chaos test report in `loadtest/report.md`                                                                            | Not started |
| M8  | Open source    | Bugs found in the Strands Agents SDK reported and fixed upstream                                                                                              | Not started |

## MVP scope (M0 + M1)

The MVP is one real agent run end to end on a laptop: publish the issue-triage spec, start a run by
hand, and watch a real model (MiniMax through the OpenAI-compatible adapter) label a real GitHub
issue. No AWS account is needed until M6.

Left out of the MVP on purpose, each added by a later milestone: the worker's run-state table,
leases, checkpoints and idempotency (M2); approvals and Slack (M3); the tool gateway, OAuth and run tokens (M4); evals
(M5); AWS deploy and OpenTelemetry (M6). One hard-coded tenant and API key is enough.

Tasks are in build order. Each line is one work session; `[component]` is the name to start it with.

## M0 tasks: walking skeleton (stub model, no AWS)

- [x] `[contracts]` `agent-spec.schema.json`, `run-message.schema.json`, a valid and an invalid
  ```
  example, one test that validates both. Check: test passes.
  ```
- [x] `[control-plane]` Spring Boot skeleton, `AgentStore` port with in-memory and DynamoDB
  ```
  adapters and one contract suite, `POST /agents`, `POST /agents/{id}/versions` (schema
  validation), one hard-coded tenant. Check: contract suite passes on both adapters; publish
  `contracts/examples/agent-spec.valid.yaml` with curl; invalid spec is rejected.
  ```
- [x] `[local-stack]` `docker-compose.yml` with DynamoDB Local (control-plane table created on
  ```
  start from `control-plane/dynamodb/control-plane-table.json`) and ElasticMQ (run queue + DLQ
  created on start), `.env.example`, `Makefile` with `up` / `down`. Check: `make up`, table and
  queue are listed.
  ```
- [x] `[contracts]` `internal-api.openapi.yaml`: `GET/PATCH /internal/runs/{id}` request and
  ```
  response shapes, SigV4 security scheme, run statuses. Check: file passes an OpenAPI linter.
  ```
- [x] `[control-plane]` `RunStore` port (in-memory + DynamoDB, contract suite), `POST /runs`,
  ```
  `GET /runs/{id}`, `RunQueuePublisher` (SQS + in-memory), internal
  `GET/PATCH /internal/runs/{id}`. Check: run item is `QUEUED` and a message is on the queue.
  ```
- [x] `[worker]` `core/handle_run.py`, ports, in-memory adapters, stub model, one fake tool,
  ```
  budget (steps and seconds). Check: integration test runs a spec to `SUCCEEDED` with no
  network.
  ```
- [x] `[worker]` `queue_sqs.py`, `control_plane_http.py`, `poll_loop.py`, `config.py`, `wiring.py`.
  ```
  Check: with the stack up, a run started by curl reaches `SUCCEEDED`.
  ```
- [x] `[local-stack]` add both services to compose, `make demo` script, README quickstart.
  ```
  Check: fresh clone → `make up && make demo` passes. **M0 done.**
  ```

## M1 tasks: real agent

- [x] `[contracts]` spec `model` becomes `<provider>/<model id>` (ADR 0010): schema description and
  ```
  a pattern requiring the `/`; examples use `stub/demo` (or `minimax/MiniMax-M3` where a real
  model is meant). Check: contracts tests pass; record under "Cross-service changes".
  ```
- [x] `[worker]` model router (ADR 0010): `Model.complete` takes the model string,
  ```
  `adapters/model_router.py`, `MODEL_PROVIDERS` / `MODEL_<NAME>_*` config replacing
  `MODEL_PROVIDER`, stub as provider `stub`; `adapters/model_openai_compat.py` (httpx or the
  `openai` SDK; decide in session) with MiniMax as first provider; `core/pricing.py`, dollar
  budget, cost reporting. Check: stub tools, MiniMax model, run succeeds and reports cost;
  router and adapter pass the `Model` contract suite; recorded-response tests for MiniMax.
  ```
- [x] `[worker]` model retries (throttling, 5xx: backoff, then `ModelError`) in the model adapters,
  ```
  and conversation trimming in core when the context nears the model's limit. Check: unit
  tests with a stub that throttles and a long scripted run.
  ```
- [x] `[worker]` `tools_local.py`: `github.search_issues`, `github.add_label` with a personal
  ```
  token. Check: unit tests with recorded responses.
  ```
- [x] `[specs]` final `issue-triage.yaml`. Check: a manual run against a test repo labels a real
  ```
  issue and finds a planted duplicate. **M1 done: MVP.**
  ```
- [x] `[worker]` (optional) `adapters/model_bedrock.py` (Converse API or Strands Bedrock provider,
  ```
  ADR 0009, ADR 0010). Needs AWS credentials and Bedrock model access; keep them out of
  `.env`'s fake local keys. Check: same `Model` contract suite, one real run on Bedrock.
  ```
- [x] `[control-plane]` (optional) `POST /triggers/github` with HMAC check; deliver webhooks
  ```
  locally with smee.io. Check: opening an issue starts a run.
  ```

## M2 tasks: crash safety

- [x] `[worker]` `RunStateStore` port (architecture → Ports and adapters) with in-memory and
  ```
  DynamoDB adapters: lease (take with expiry, renew, release; refused while another worker's
  lease is live), checkpoint (step n only after n-1, read latest), idempotency (claim a key,
  store its result, read it). Table keys in one file, `worker/dynamodb/run-state-table.json`.
  Check: one contract suite passes on both adapters (DynamoDB Local), including two concurrent
  takers where exactly one wins.
  ```
- [x] `[local-stack]` create the run-state table from that file in `make stack` / `make up`;
  ```
  worker gets `STATE_BACKEND=dynamodb`, `STATE_TABLE`. Check: `make up && make demo` passes.
  ```
- [x] `[worker]` lease in the poll loop: take on receive, renew with the heartbeat, release when
  ```
  `handle_run` returns; a run leased by another live worker is left alone (message not deleted).
  Check: two workers given the same run, only one executes it.
  ```
- [x] `[worker]` checkpoints: write after every model turn and every tool result (messages,
  ```
  usage, cost, `provider_data`); a picked-up run resumes from the latest. Check: integration
  test stops a run after step k, a second `handle_run` finishes it with no repeated model calls
  for steps 1..k and the same budget totals.
  ```
- [x] `[worker]` idempotent tool calls: key `hash(run_id, step, tool, arguments)`, claim before
  ```
  calling, store the result after; a resumed run gets the stored result. Decide in session (ADR
  if it is a rule) what a claimed key without a result means after a crash: retry, fail the
  run, or per-tool. Check: crash between the tool call and the checkpoint, resume, the fake
  GitHub server sees one `add_label`.
  ```
- [ ] `[control-plane]` re-enqueue sweep: a scheduled job finds runs left `QUEUED` longer than a
  ```
  few minutes (saved, but the enqueue failed or was lost) and sends their message again.
  `POST /runs` already returns 503 and leaves the run `QUEUED` when enqueueing fails. Needs a
  query by status (new index or a scan). Check: a run whose enqueue failed reaches the queue.
  ```
- [ ] `[control-plane]` webhook redelivery dedupe on `X-GitHub-Delivery` (conditional put, short
  ```
  TTL). Check: the same delivery twice starts one run.
  ```
- [ ] `[local-stack]` chaos demo: `make chaos` kills the worker container mid-run (stub model,
  ```
  fake tools that count calls), restarts it, and checks the run succeeds with no duplicate tool
  call. **M2 done.**
  ```

## Cost guardrails

- Set an AWS Budgets alert before deploying anything, and a spend limit or alert on every model
  provider account (MiniMax, OpenRouter, ...).
- Nothing billed per hour (ADR 0007): no NAT gateway, load balancer, RDS, interface VPC endpoints
  or always-on instance. Public subnets for Fargate tasks; Lambdas outside the VPC.
- Before sharing the URL with others: API Gateway route throttling, per-run budgets enforced, and a
  daily model spend alarm.
- Use the cheapest capable model for evals; use recorded fixtures so evals never call real
  APIs.
- Workers scale to 0 when idle; run load tests only against the stub model.

## Open questions

- Will the repo be public? Affects how much personal context belongs in it.
- GitHub App vs per-user OAuth for the first GitHub connector.
- Is the worker's scale-from-0 delay (a minute or more) acceptable for demos, or add the Lambda
  worker entrypoint (ADR 0005) early?
- Is Spring Boot's cold start on Lambda with SnapStart fast enough for Slack's 3-second callback
  limit, or does the control plane need a lighter framework?
- k6 or Locust for the load driver.
- Gateway `TokenStore`: Secrets Manager costs $0.40 per secret per month, which grows with every
  stored OAuth token. SSM Parameter Store SecureString (standard tier, free) may be the better
  adapter. Decide in M4.

## Cross-service changes

Changes to `contracts/` that another service still has to adopt. Remove an entry once every listed
service has followed.

- **Internal API and gateway auth is IAM (SigV4) through API Gateway** (ADR 0007). Declared in
  `contracts/internal-api.openapi.yaml`. Must follow: `worker` (`tools_mcp.py` in M4 signs
  gateway calls; `control_plane_http.py` done), `tool-gateway` (signs calls to the control plane).

## Handoff notes

Newest first. One entry per work session: date, component, done, next, gotchas. Trim entries older
than the current milestone.

- **2026-11-17 · worker** — Idempotent tool calls (ADR 0013). `ToolSpec.read_only` /
  `idempotent` (MCP annotation names; fake and GitHub tools flagged, `slack.post_message` not).
  `_execute`: claim key `tool_call_key(run_id, step, call)` (SHA-256), call, store
  `{content, is_error}`, then checkpoint; stored result reused on resume; claimed without result:
  repeat if safe, else FAILED "Outcome unknown: ...". Tests: 261 passed (7 new, incl. the
  roadmap check: worker killed after `add_label` reached the fake GitHub server, resumed, one
  POST). Live: `make demo` stored two `TOOL#` items with results. Next: `[control-plane]`
  re-enqueue sweep or webhook dedupe, then `[local-stack]` `make chaos`. Gotchas: claim, store
  and checkpoint are separate writes, so the claim-to-store window is where "unknown" comes
  from; a run failed for unknown outcome needs a person to check and rerun; `make demo`
  republished the stub `issue-triage`, real spec now version 9.
- **2026-11-16 · worker** — Checkpoints and resume (ADR 0011; ADR 0012: time budget counts
  working time summed over attempts). After every step `handle_run` saves the messages it added
  (`Message.to_json`), its cost, `elapsed_s` and (model turns) `stop_reason`; resume rebuilds
  the history, continues budget totals, reports `RUNNING` with the saved steps, runs only tool
  calls of the latest turn without a saved result (matched after that turn: providers may reuse
  call ids), reports a saved final answer without a model call. Loop keeps full history, trims a
  copy. `Attempt(owner, cancelled)` replaces `cancelled`; `Deps.state`. `StateError` stops the
  attempt (no FAILED). Tests: 255 passed (11 new, `test_resume.py` + JSON round trip). Live:
  `make demo` wrote `STEP#000001..5` (140-220 bytes each). Next: `[worker]` idempotent tool
  calls. Gotchas: a crash between a tool call and its checkpoint re-runs that call (task 5
  fixes); a resumed run stopped at a `require_approval` call pauses again (M3 must pass the
  approval in); a step over 350 KB raises `StateError` on every attempt until the DLQ, the run
  stays `RUNNING` (tool results are capped, so unlikely); `make demo` republishes the stub
  `issue-triage`, real spec republished as version 7.
- **2026-11-16 · worker** — Lease in the poll loop: take before the run (owner per attempt
  `<host>-<pid>/<random>`), renew in the heartbeat, release in `finally`; held by another
  worker: message kept and hidden one lease length; `StateError` on take: message kept.
  `handle_run(run_id, deps, cancelled=None)`: checked before every step with the budget; true
  raises `LeaseLost` (no status reported, message kept). Config `STATE_BACKEND=dynamodb|memory`
  (default dynamodb), `STATE_TABLE` (default `run-state`); `App.state`, `PollLoop(queue, deps,
  state, ...)`. Tests: 244 passed (9 new: duplicate across two loops and within one, lease lost
  mid-run stops before `add_label`, renewal past expiry, release after error, store outage,
  config). Live: `make demo` SUCCEEDED on the DynamoDB store, `LEASE` item released
  (`expires_at` 0). Next: `[worker]` checkpoints. Gotchas: handlers passed to `PollLoop` take
  `(run_id, deps, cancelled)`; a duplicate message for a long run can reach the DLQ (harmless,
  the holder has its own); the memory store protects one process only; `make demo` republishes
  `issue-triage` with the stub spec, republish `specs/issue-triage.yaml` after (now version 5).
- **2026-11-16 · local-stack** — `local-stack/create-tables.sh` (renamed from
  `create-table.sh`) creates `control-plane` and `run-state` from each service's files
  (`worker/dynamodb` mounted at `/defs/worker`) and enables TTL on `ttl`; each step checks first,
  so a second run is a no-op. Worker container gets `AWS_ENDPOINT_URL_DYNAMODB`;
  `.env.example` (and the local `.env`) `STATE_BACKEND=dynamodb`, `STATE_TABLE=run-state`, not
  read until the lease task. Checked: `make status` lists both tables, `describe-time-to-live`
  ENABLED, `make demo` SUCCEEDED (5 steps). Next: `[worker]` lease in the poll loop (adds
  `STATE_*` config and wiring). Gotchas: `make demo` fails with `TOOLS_PROVIDER=local`
  (`Unknown tools: github.close_issue, slack.post_message`); run it with a compose override
  setting `TOOLS_PROVIDER: fake` for the worker; `make down` wiped the hand-published agents,
  real `specs/issue-triage.yaml` republished as version 3 (`issue-triage-nova` is gone).
- **2026-11-16 · worker** — M2 task list written and ordered (roadmap). `RunStateStore` port
  (`ports/run_state.py`) with `run_state_memory.py` and `run_state_dynamodb.py` (ADR 0011: one
  item per step, fenced writes, 15-day TTL); table files `worker/dynamodb/run-state-table.json`
  and `run-state-ttl.json`; errors `LeaseLost`, `CheckpointConflict`, `StateError`;
  `Clock.time()` (wall clock) added. Tests: 235 passed (45 new): contract suite on both adapters
  (DynamoDB Local via Testcontainers, a table per test from the files), including 8 racing
  takers and a stale worker fenced out; Stubber tests for DynamoDB errors. Not wired yet: no
  `STATE_*` config until the lease task. Next: `[local-stack]` create the table (and enable TTL
  from `run-state-ttl.json`: `aws dynamodb update-time-to-live --cli-input-json`). Gotchas:
  `owner` and `ttl` are DynamoDB reserved words (use `#` placeholders); DynamoDB rejects
  `ExpressionAttributeNames` entries an expression does not use; a racing transaction surfaces
  as `StateError` (`TransactionConflict`), not `CheckpointConflict`, so the lease task decides on
  retry; a claim with no result means the claimer may or may not have called the tool (task 5
  decides); approval waits (M3) must stay under 15 days or the run's state expires.
- **2026-11-16 · worker** — `adapters/model_bedrock.py`: Bedrock Converse via plain boto3
  (`bedrock-runtime` `converse`), lazy client, botocore standard retries (4 attempts), errors to
  `ModelError`. Config: `MODEL_<NAME>_PROTOCOL=bedrock`, `_AWS_PROFILE`, `_REGION`,
  `_TEMPERATURE` (new for both protocols); `BASE_URL` now required only for `openai`. Price row
  `bedrock/amazon.nova-lite-v1:0` ($0.06 / $0.24 per 1M tokens, 300k context, AWS price list).
  Tests: 190 passed (20 new): adapter unit tests on botocore `Stubber`
  (`tests/fake_bedrock.py`), `Model` contract suite on real Nova Lite recordings
  (`tests/fixtures/bedrock/`), config/wiring. Live: agent `issue-triage-nova` (local stack only,
  trigger `manual.bedrock.test`) on sandbox #6 (planted duplicate of #3): `SUCCEEDED`, 9 steps,
  8 s, $0.000485, labeled `bug` + `duplicate`, but it searched with the full title + body, never
  found #3, named itself as the duplicate with low confidence and still added `duplicate`. Adapter
  fine, Nova Lite judgment weak: keep `minimax/MiniMax-M3` for `specs/issue-triage.yaml`; try Nova
  2 Lite ($0.33 / $2.75, needs a price row) if Bedrock matters. Next: M2. Gotchas: Nova fails with
  `ModelErrorException: Model produced invalid sequence as part of ToolUse` on hyphenated tool
  names (hence `_`); Nova wraps text in `<thinking>`/`<response>` (stripped); a named profile is
  what keeps `.env`'s fake `AWS_ACCESS_KEY_ID=local` out of Bedrock calls, never put real AWS keys
  in `.env`; the Docker worker has no `~/.aws`, so Bedrock runs only work from a host worker;
  rebuild the worker image after config changes (`docker compose up -d --build worker`) or it
  rejects `PROTOCOL=bedrock`.
- **2026-11-17 · control-plane** — `POST /triggers/github` (package `trigger`):
  `GitHubWebhooks` (HMAC check, trigger name, run input) + `GitHubWebhookController`;
  `RunService.startForTrigger` (configured tenant only, sorted by agent name);
  `WebhookRejectedException` mapped in `ApiExceptionHandler`. Config `GITHUB_WEBHOOK_SECRET`
  (`platform.github.webhook-secret`; unset = 503). `issues` events trimmed to the fields agents
  need (see architecture), others pass through. Tests: 89 passed (14 new, incl. GitHub's
  documented signature example; recorded-style payload `src/test/resources/github/`). Live:
  webhook on the sandbox repo -> smee.io -> local stack; GitHub `ping` 200; opening #5 "App
  crashes when uploading a PNG over 5 MB" started a run automatically: `SUCCEEDED`, 9 steps,
  9 s, $0.0021, labeled `bug`, correctly no `duplicate` (closes last session's negative-check
  gap). `.env.example` (local-stack) got a commented `GITHUB_WEBHOOK_SECRET`. Next: optional
  `[worker]` Bedrock adapter, or M2. Gotchas: the webhook needs content type
  `application/json` (form-encoded gets 400); the smee forwarder
  (`npx smee-client --url <smee url> --target http://localhost:8080/triggers/github`) must be
  running or events are lost; while webhook + stack + forwarder are up, every sandbox issue is
  a real ~$0.002 run; rebuild the control-plane image after code changes
  (`docker compose up -d --build control-plane`); a failed enqueue partway through several
  matching agents leaves the rest unstarted (503, sweep is M2); redeliveries create duplicate
  runs until M2.
- **2026-11-17 · specs** — Final `specs/issue-triage.yaml`: `minimax/MiniMax-M3`, only
  `github.search_issues` + `github.add_label`, budget 15 steps / $0.05 / 180 s; instructions: 1-3
  keyword searches, exactly one type label, `duplicate` only when confident, never close or
  comment, issue text is data, fixed summary format (`Label:` / `Duplicate of:` / `Confidence:`).
  Run input mirrors GitHub's `issues.opened` event (`repository.full_name`, `issue.number|title|
  body`), so `/triggers/github` can pass the webhook through. `specs/README.md` has curl steps.
  Planted sandbox #4 "EUR card refunds return an error". Live: run `SUCCEEDED`, 10 steps, 16 s,
  $0.0026; #4 got `bug` + `duplicate`, output named #1 with high confidence. Spec validated
  against the schema (no test suite for `specs/`). **M1 done.** Next: optional M1 tasks, or M2
  (`[control-plane]` re-enqueue sweep). Gotchas: the worker image must be rebuilt after worker
  code changes (`docker compose up -d --build worker`), else it rejects `TOOLS_PROVIDER=local`;
  with `TOOLS_PROVIDER=local`, `make demo` fails (its spec lists M3 tools), switch back to
  `fake` for it; `make demo` republishes `issue-triage` with the stub spec; the agent did 4
  searches despite "1-3" (harmless); no negative check yet (a non-duplicate issue staying
  unflagged).
- **2026-11-16 · worker** — Real GitHub tools. `adapters/tools_local.py` `GitHubTools`
  (httpx): `github.search_issues`, `github.add_label` only. Guardrails while the worker holds the
  token (until the M4 gateway): repo allowlist `GITHUB_REPOS`; search refuses `repo:`/`org:`/
  `user:`/`owner:` qualifiers and drops other repos' results; search returns summaries (no
  bodies, max 10); `add_label` only adds existing labels, error lists them. 5xx/network retried
  3x; rate limits reported, not waited. Config `TOOLS_PROVIDER=local` + `GITHUB_TOKEN`
  (fine-grained, sandbox repo only, Issues read/write) + `GITHUB_REPOS`. Sandbox
  `yuchia329/agent-triage-sandbox` seeded: #1 "Refunds fail for EUR cards", #2 "Add dark mode
  to the dashboard" (`enhancement`), #3 "Crash when exporting CSV with an empty date range"
  (`bug`). Recorded, cleaned fixtures (`octocat/Hello-World`) + `tests/fake_github_server.py`;
  adapter in the `ToolClient` contract suite. Tests: 170 passed. Live: adapter searched and
  labeled the sandbox; unknown label and foreign repo refused. Next: `[specs]` final
  `issue-triage.yaml` (model `minimax/MiniMax-M3`, only the two GitHub tools), plant a duplicate
  of #1, run end to end. Gotchas: a spec listing `close_issue`/`slack.post_message` fails at run
  start with `TOOLS_PROVIDER=local` (intended; M3); new issues take seconds to reach GitHub's
  search index; the token expires (30 days), then make a new one.
- **2026-11-16 · worker** — Model retries and conversation trimming. `model_openai_compat.py`
  retries 429/5xx, transport errors and MiniMax rate-limit/timeout/server codes in `base_resp`
  (list from the official error-code page, in `RETRY_PROVIDER_CODES`): 4 attempts, backoff
  ~1/2/4 s with jitter, `Retry-After` honored up to 30 s; 4xx, auth, balance, bad replies fail
  at once. `core/pricing.py` became `core/models.py` (`ModelInfo(price, context_tokens)`;
  `Deps.models` lets tests swap the table). New `core/trim.py`: before each call, past 70% of
  the context window, shorten old tool results and reasoning, then drop oldest exchanges (note
  added to the run input), else `FAILED` "does not fit"; last 2 exchanges and call/result pairs
  always kept; size estimate is chars x tokens-per-char learned from each reply's
  `input_tokens`. Tests: 143 passed (22 new); `make demo` passes; no real model calls. Next:
  `[worker]` `tools_local.py`. Gotchas: a retried timeout may be billed twice; MiniMax's 1M
  context means trimming practically never fires there, it matters for small self-hosted
  models; `make demo` republishes `issue-triage` with `stub/demo`, so republish the MiniMax
  spec before a real run.
- **2026-11-16 · worker** — Real model runs (ADR 0010). `Model.complete(model, ...)`;
  `adapters/model_router.py` splits `<provider>/<model id>`; `adapters/model_openai_compat.py`
  (plain httpx, no SDK, no retries yet) maps tool names `.` <-> `-`, keeps MiniMax's
  `reasoning_content` in the new opaque `provider_data` on `ModelTurn`/`Message` and sends it
  back, treats a reply without `usage` or with a MiniMax `base_resp` error as `ModelError`.
  `core/pricing.py` (MiniMax M3 $0.30/$1.20, M3.1 Flash $0.15/$0.60 per million, official page
  checked); unpriced model fails before step 1; budget checks dollars; every final/paused update
  carries `cost_usd`. Config `MODEL_PROVIDERS` + `MODEL_<NAME>_PROTOCOL|BASE_URL|API_KEY| MAX_TOKENS|EXTRA_BODY`; old `MODEL_PROVIDER` is a config error. Recorded MiniMax fixtures +
  `tests/fake_openai_server.py`. `.env.example` (local-stack) updated by necessity. Tests: 121
  passed. Live: `make demo` (stub) passes; a `minimax/MiniMax-M3` run with fake tools reached
  `SUCCEEDED` in 16 steps, $0.0045, 44 s. Cross-service entry for the model format removed (all
  services follow). Next: `[worker]` model retries + conversation trimming. Gotchas: JSON env
  values need single quotes in `.env` (`'{"reasoning_split":true}'`), or `sh` strips the inner
  quotes; MiniMax thinking tokens count toward `MAX_TOKENS`; zsh `echo` turns `\n` in JSON into
  real newlines when scripting curl checks.
- **2026-11-16 · contracts** — Spec `model` is now `<provider>/<model id>` (ADR 0010): schema
  pattern `^[a-z][a-z0-9_]{0,31}/\S+$` (provider is lowercase letters, digits, `_`, since it
  becomes part of `MODEL_<NAME>_*` env names; the model id may contain `/`), description updated,
  examples use `stub/demo`, tests for 5 rejected and 3 accepted formats. Also changed by decision:
  `control-plane` `SpecValidatorTest` fixture `"m"` → `"stub/m"` (one line, keeps its suite green).
  Tests: contracts 49 passed, control-plane 75 passed, worker 73 passed (offline). Next: `[worker]`
  model router + OpenAI-compatible adapter; it must route `stub/...` to the stub so `make demo`
  keeps working. Gotchas: specs stored with the old format would now fail to publish again (local
  data only, none kept); a crashed Gradle daemon can leave
  `control-plane/.gradle/<version>/executionHistory/` corrupt (see `control-plane/AGENTS.md`).
- **2026-11-16 · docs** — Model layer made provider-neutral (ADR 0010): spec `model` becomes
  `<provider>/<model id>`; the worker config defines providers (`MODEL_PROVIDERS`,
  `MODEL_<NAME>_PROTOCOL|BASE_URL|API_KEY`); one adapter per protocol (OpenAI-compatible covers
  MiniMax, OpenAI, OpenRouter, Together, Gemini, vLLM, Ollama; Bedrock Converse) behind a router.
  MiniMax is the first real provider, chosen for cost (about $0.30 / $1.20 per million tokens,
  no deposit fee; OpenRouter adds 5.5%, min $0.80, per top-up). Bedrock adapter moved to an
  optional M1 task, so M1 needs no AWS account. Docs only; no code changed. Next: `[contracts]`
  `provider/model` format, then `[worker]` router + OpenAI-compatible adapter. Gotchas: provider
  API keys go in `.env` (git-ignored) as `MODEL_<NAME>_API_KEY`, never in tracked files; check
  MiniMax's current model id, base URL and price on its own docs before coding.
- **2026-11-16 · local-stack** — M0 done. Compose now builds and runs `control-plane` (healthcheck)
  and `worker` next to DynamoDB Local and ElasticMQ; per-service env overrides swap `localhost`
  for service names. `control-plane/Dockerfile` and `worker/Dockerfile` (multi-stage, build inside
  Docker, non-root; context is the repo root) live in the service folders by decision: packaging
  belongs to the service, and the worker image is the M6 Fargate image. Root `.dockerignore`
  keeps `notes/`, `.env`, build output out. `Makefile`: `up` (build, start, wait), `stack` (AWS
  stand-ins only, for host runs), `demo` (`local-stack/demo.sh`, sh + curl, exits non-zero unless
  `SUCCEEDED`). README quickstart. Checked: `make down`, `.env` deleted, `--no-cache` build, then
  `make up && make demo` passed. Also fixed in `worker/`: boto3 ignores `AWS_REGION` (reads only
  `AWS_DEFAULT_REGION`), so `SqsRunQueue` takes the region from config (regression test); it had
  only worked on the host because of `~/.aws/config`. Next: M1 `[worker]` Bedrock model adapter.
  Gotchas: `make up --wait` can report the worker healthy just before it exits (no healthcheck);
  `make demo` prints worker logs on failure. First build takes minutes (Gradle and uv caches are
  BuildKit cache mounts afterwards). Host `bootRun` / `uv run worker` need `make stack`, not
  `make up` (port 8080 and the queue are taken).
- **2026-11-16 · worker** — Worker runs end to end against the local stack: `RunQueue` port
  (SQS + in-memory, one contract suite; SQS runs on ElasticMQ via Testcontainers),
  `control_plane_http.py` (SigV4 `execute-api`, retries connection errors / 429 / 5xx, joins the
  `ControlPlane` suite against a fake server that checks requests against the contract),
  `entrypoints/poll_loop.py` (concurrency slots, heartbeat, graceful SIGTERM), `config.py`,
  `wiring.py`, `StubModel` (stateless, `demo_turns()` script), `core/run_message.py`. `uv run worker` starts it. 78 tests pass (72 with `-m "not docker"`). Checked by hand: stack + control
  plane `bootRun` + worker, a curl-started run reached `SUCCEEDED` (5 steps), queue and DLQ empty.
  Worker vars added to `.env.example`. Next: `[local-stack]` both services in compose, `make demo`.
  Gotchas: message deleted once `handle_run` returns; kept (retry, then DLQ) on invalid body,
  unknown run, or control plane outage. Two workers can still both run one run (`RUNNING → RUNNING` allowed) until the M2 lease. Run message is parsed by hand-written checks (the image
  will not ship `contracts/`); a test keeps them in line with the schema. In compose, set
  `CONTROL_PLANE_URL=http://control-plane:8080` and the ElasticMQ host change from the local-stack
  note below.
- **2026-11-16 · worker** — Decided the worker owns the agent loop (ADR 0009; Strands only behind
  the `Model` port). New `worker/` (Python 3.12, uv, pytest, ruff): `core/handle_run.py`,
  `core/budget.py` (steps, seconds), `core/types.py`; ports `ControlPlane`, `Model`, `ToolClient`,
  `Clock`; adapters in-memory control plane (same status rules), scripted model, fake tools
  (record calls). Loop refuses tools not in the spec, validates arguments against tool schemas,
  pauses on `require_approval`, treats refusal / max-tokens as failure, ends crashes as `FAILED`.
  29 tests pass, no network; the test run is `contracts/examples/internal-run.valid.json` and every
  update is checked against the contract's `RunUpdate`. A test enforces "core imports no vendor
  SDK". Next: `[worker]` SQS queue, control-plane HTTP adapter (joins the `ControlPlane` contract
  suite), poll loop. Gotchas: a redelivered `RUNNING` run restarts from step 1 (checkpoints M2);
  no cost reported yet (M1); resume after approval is M3.
- **2026-11-16 · control-plane** — `RunStore` port (in-memory + DynamoDB, 11-test contract suite
  incl. concurrent finishes), `RunQueuePublisher` (in-memory + SQS, contract suite checks bodies
  against `run-message.schema.json`), `POST /runs`, `GET /runs/{id}`, `GET/PATCH /internal/runs/{id}`. `ContractValidator` validates against the bundled `contracts/` files
  (PATCH bodies vs `RunUpdate`; tests check responses vs `InternalRun` / `RunSummary`). Status
  rules in `RunStatus.allowedFrom()`. Public API switched to snake_case, nulls omitted. 75 tests
  pass; checked against the local stack (run `QUEUED` in DynamoDB, message on `runs`, internal
  GET/PATCH work). Next: `[worker]` `handle_run` with in-memory adapters. Gotchas: enqueue failure
  returns 503 and leaves the run `QUEUED` (sweep task added under M2); `/internal/*` has no auth
  locally (SigV4 at API Gateway in AWS); `steps` is a count.
- **2026-11-16 · contracts** — Added `internal-api.openapi.yaml` (OpenAPI 3.1): GET/PATCH
  `/internal/runs/{run_id}`, SigV4 scheme (`x-amazon-apigateway-authtype: awsSigv4`), status change
  table, problem+json errors. Spec in the GET response `$ref`s `agent-spec.schema.json`. Examples
  for the GET response and PATCH body; `tests/test_internal_api.py` (41 tests total, all pass).
  Redocly lint: valid, one warning (no `license`; waits on the "public repo?" question). Next:
  `[control-plane]` runs task, implementing this contract. Gotchas: `steps` is a count (per-step
  detail comes with M2 checkpoints); WAITING_APPROVAL → QUEUED and rejection → FAILED are done by
  the control plane, not via PATCH; shared run fields live in `RunFields`, and each composed
  schema closes itself with `unevaluatedProperties: false`.
- **2026-11-16 · local-stack** — `docker-compose.yml` with DynamoDB Local 3.3.2 (in memory),
  ElasticMQ 1.7.1 (`runs` queue, 60 s visibility, `runs-dlq` after 5 receives) and a one-off
  `aws-init` container that creates the control-plane table from
  `control-plane/dynamodb/control-plane-table.json`. `.env.example`, `Makefile` (`up`, `down`,
  `status`, `logs`). Checked: `make up` + `make status` list the table and both queues; re-running
  `make up` is safe; the control plane with `STORE_BACKEND=dynamodb` publishes against it. Next:
  `[contracts]` internal API. Gotchas: Gradle does not read `.env` (load it with `set -a`, see
  `local-stack/AGENTS.md`); queue URLs use `localhost:9324`, which must change to
  `elasticmq:9324` when services move into compose (task 8); `elasticmq-native` has no web UI,
  only stats on 9325.
- **2026-11-16 · control-plane** — Spring Boot 4 skeleton (Gradle 8.14, JDK 21 auto-downloaded by
  Gradle's daemon JVM setting), `AgentStore` port with in-memory and DynamoDB adapters and one
  contract suite, `POST /agents`, `POST /agents/{name}/versions` (YAML or JSON, schema check,
  `require_approval ⊆ tools`, name must match), `X-Api-Key` auth for one tenant from config.
  33 tests pass (DynamoDB suite on Testcontainers DynamoDB Local); curl check done. Table keys and
  indexes fixed in `control-plane/dynamodb/control-plane-table.json` (generic `PK`/`SK`, `GSI1`,
  `GSI2`). Reordered roadmap: control plane before local stack. Docs: Spring Boot 4, plain DynamoDB
  client, per-service endpoint env vars. Next: `[local-stack]` (create the table from that JSON
  file with the AWS CLI's `--cli-input-json`). Gotchas: Docker must run for `./gradlew test`;
  "tool names exist in the gateway" check is deferred to M4; every publish becomes active until
  `PUT active-version` exists.
- **2026-11-16 · contracts** — Added agent-spec and run-message JSON Schemas (draft 2020-12),
  valid/invalid examples in `contracts/examples/`, and a pytest suite (17 tests, all pass). The
  valid spec example is the demo issue-triage agent and replaces `specs/issue-triage.yaml` for M0.
  Removed `version:` from the `product.md` spec example. Added a "Where to start" build-order section
  and a new `[contracts]` task for `internal-api.openapi.yaml`. Next: `[local-stack]` or
  `[control-plane]` publish task (needs local-stack for the DynamoDB adapter, or use Testcontainers).
  Gotcha: JSON Schema cannot express `require_approval ⊆ tools`; the control plane must check it.
- **2026-11-16 · docs** — Redesigned the deploy for zero idle cost: control plane and tool gateway
  on Lambda behind one API Gateway HTTP API with a Cloudflare-managed custom domain; control-plane
  data moved from PostgreSQL to DynamoDB with `AgentStore` / `RunStore` ports (ADR 0007, ADR 0008;
  ADR 0004's database exception superseded). Postgres on the owner's EC2 with a VPC Lambda was
  considered and rejected (NAT needed for outbound; see ADR 0008). M0 tasks updated: no Postgres,
  no Flyway. Next: M0,
  `[contracts]`. Gotcha: design the control-plane key schema from the access-pattern table in
  architecture.md before writing the DynamoDB adapter.
- **2026-11-16 · docs** — Design docs, ADRs 0001–0006 and session commands created. Next: M0,
  starting with `contracts/` and `docker-compose.yml`.
