# Roadmap

Milestones, what each one built, and what is left for later. The other docs describe the design.

**Status: complete (2026-11-21).** Milestones M0 to M8 are done and deployed on AWS (dev). The
live agent `issue-triage` answers new issues in the sandbox repo through the GitHub App; load-test
mode is off and the workers are at 0 when idle. What each milestone delivered:

- **M0 local skeleton:** `make up && make demo` publishes a spec and runs it end to end with a stub
  model and fake tools.
- **M1 real agent:** issue-triage on MiniMax (`minimax/MiniMax-M3`) with real GitHub tools; any
  OpenAI-compatible provider or Bedrock by config (ADR 0010).
- **M2 crash safety:** lease, per-step checkpoints, idempotent tool calls (ADR 0011-0013);
  `make chaos` kills a worker mid-run and the run adds its label exactly once.
- **M3 approvals:** closing a duplicate waits for Approve in Slack; Reject or 72 h without a
  decision fails the run.
- **M4 tool gateway:** every tool call goes through the gateway over MCP with a signed run token;
  connectors generated from OpenAPI; the GitHub App as team bot, user login for `acts_as: owner`.
- **M5 evals:** `make eval` on the real model against a simulated GitHub, an LLM judge, committed
  baselines (issue-triage 29/30 tasks, needs-info 8/8).
- **M6 AWS deploy:** Terraform, scale to zero (ADR 0007): Lambdas behind one HTTP API, Fargate
  workers from 0, traces in X-Ray, throttling and a spend alarm; idle cost about $1 a month.
- **M7 scale proof:** 1,000 runs all `SUCCEEDED`, up to 918 at once; chaos at 1,000 runs, 0 lost,
  0 duplicate side effects (`loadtest/report.md`).
- **M8 production limits:** tenants with hashed keys, a cap on runs at once, set-aside on model
  429s, daily spend caps, a fair queue; on AWS 0 of 1,197 runs failed with 20% 429s, the burst
  tenant held its cap and the quiet one started within 0.1 s (p95) (`loadtest/report.md`).

## Approach

Build a thin slice through every service first, then deepen each part. Do not finish one service
before starting the next. Each milestone ends with something demoable.

## Picking it up again

A new session starts with `/start <component>` (AGENTS.md → Session start) and takes a task from
"Future work" below; each task names its component. The milestone task lists further down are the
record of what was built, in order.

## Milestones

| #   | Milestone      | Done when                                                                                                                                                     | Status      |
| --- | -------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------- |
| M0  | Local skeleton | `docker compose up`; publish a spec; one run completes end to end with the stub model                                                                         | Done        |
| M1  | Real agent     | Issue-triage agent runs on a real model (MiniMax) and calls GitHub directly from the worker (personal token)                                                  | Done        |
| M2  | Crash safety   | Lease, checkpoints, idempotency; killing a worker mid-run causes no duplicate tool call                                                                       | Done        |
| M3  | Approvals      | Close-issue pauses; Slack buttons resume or reject the run                                                                                                    | Done        |
| M4  | Gateway        | Tool calls move into the gateway; OpenAPI → MCP generator; OAuth and team bots                                                                                | Done        |
| M5  | Evals          | `make eval` scores any spec that has eval tasks (real model, simulated services) and flags a regression; issue-triage and a second spec                        | Done        |
| M6  | AWS deploy     | Terraform deploys everything scale-to-zero (ADR 0007): Lambdas behind one API Gateway HTTP API, workers at 0; traces in X-Ray; no hourly cost when idle | Done        |
| M7  | Scale proof    | 1,000 concurrent runs on the stub model against the AWS deployment; chaos test report in `loadtest/report.md`                                                 | Done        |
| M8  | Production limits | Two tenants on AWS: while tenant A starts 1,000 runs at once and tenant B 1 a second, B's runs start within 30 s (p95, workers up) and A never exceeds its cap on runs at once; with 20% of stub model calls answering 429 no run fails; a tenant over its daily spend cap gets new runs refused; numbers in `loadtest/report.md` | Done |

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
  example, one test that validates both. Check: test passes.
- [x] `[control-plane]` Spring Boot skeleton, `AgentStore` port with in-memory and DynamoDB
  adapters and one contract suite, `POST /agents`, `POST /agents/{id}/versions` (schema
  validation), one hard-coded tenant. Check: contract suite passes on both adapters; publish
  `contracts/examples/agent-spec.valid.yaml` with curl; invalid spec is rejected.
- [x] `[local-stack]` `docker-compose.yml` with DynamoDB Local (control-plane table created on
  start from `control-plane/dynamodb/control-plane-table.json`) and ElasticMQ (run queue + DLQ
  created on start), `.env.example`, `Makefile` with `up` / `down`. Check: `make up`, table and
  queue are listed.
- [x] `[contracts]` `internal-api.openapi.yaml`: `GET/PATCH /internal/runs/{id}` request and
  response shapes, SigV4 security scheme, run statuses. Check: file passes an OpenAPI linter.
- [x] `[control-plane]` `RunStore` port (in-memory + DynamoDB, contract suite), `POST /runs`,
  `GET /runs/{id}`, `RunQueuePublisher` (SQS + in-memory), internal
  `GET/PATCH /internal/runs/{id}`. Check: run item is `QUEUED` and a message is on the queue.
- [x] `[worker]` `core/handle_run.py`, ports, in-memory adapters, stub model, one fake tool,
  budget (steps and seconds). Check: integration test runs a spec to `SUCCEEDED` with no
  network.
- [x] `[worker]` `queue_sqs.py`, `control_plane_http.py`, `poll_loop.py`, `config.py`, `wiring.py`.
  Check: with the stack up, a run started by curl reaches `SUCCEEDED`.
- [x] `[local-stack]` add both services to compose, `make demo` script, README quickstart.
  Check: fresh clone → `make up && make demo` passes. **M0 done.**

## M1 tasks: real agent

- [x] `[contracts]` spec `model` becomes `<provider>/<model id>` (ADR 0010): schema description and
  a pattern requiring the `/`; examples use `stub/demo` (or `minimax/MiniMax-M3` where a real
  model is meant). Check: contracts tests pass; record under "Cross-service changes".
- [x] `[worker]` model router (ADR 0010): `Model.complete` takes the model string,
  `adapters/model_router.py`, `MODEL_PROVIDERS` / `MODEL_<NAME>_*` config replacing
  `MODEL_PROVIDER`, stub as provider `stub`; `adapters/model_openai_compat.py` (httpx or the
  `openai` SDK; decide in session) with MiniMax as first provider; `core/pricing.py`, dollar
  budget, cost reporting. Check: stub tools, MiniMax model, run succeeds and reports cost;
  router and adapter pass the `Model` contract suite; recorded-response tests for MiniMax.
- [x] `[worker]` model retries (throttling, 5xx: backoff, then `ModelError`) in the model adapters,
  and conversation trimming in core when the context nears the model's limit. Check: unit
  tests with a stub that throttles and a long scripted run.
- [x] `[worker]` `tools_local.py`: `github.search_issues`, `github.add_label` with a personal
  token. Check: unit tests with recorded responses.
- [x] `[specs]` final `issue-triage.yaml`. Check: a manual run against a test repo labels a real
  issue and finds a planted duplicate. **M1 done: MVP.**
- [x] `[worker]` (optional) `adapters/model_bedrock.py` (Converse API or Strands Bedrock provider,
  ADR 0009, ADR 0010). Needs AWS credentials and Bedrock model access; keep them out of
  `.env`'s fake local keys. Check: same `Model` contract suite, one real run on Bedrock.
- [x] `[control-plane]` (optional) `POST /triggers/github` with HMAC check; deliver webhooks
  locally with smee.io. Check: opening an issue starts a run.

## M2 tasks: crash safety

- [x] `[worker]` `RunStateStore` port (architecture → Ports and adapters) with in-memory and
  DynamoDB adapters: lease (take with expiry, renew, release; refused while another worker's
  lease is live), checkpoint (step n only after n-1, read latest), idempotency (claim a key,
  store its result, read it). Table keys in one file, `worker/dynamodb/run-state-table.json`.
  Check: one contract suite passes on both adapters (DynamoDB Local), including two concurrent
  takers where exactly one wins.
- [x] `[local-stack]` create the run-state table from that file in `make stack` / `make up`;
  worker gets `STATE_BACKEND=dynamodb`, `STATE_TABLE`. Check: `make up && make demo` passes.
- [x] `[worker]` lease in the poll loop: take on receive, renew with the heartbeat, release when
  `handle_run` returns; a run leased by another live worker is left alone (message not deleted).
  Check: two workers given the same run, only one executes it.
- [x] `[worker]` checkpoints: write after every model turn and every tool result (messages,
  usage, cost, `provider_data`); a picked-up run resumes from the latest. Check: integration
  test stops a run after step k, a second `handle_run` finishes it with no repeated model calls
  for steps 1..k and the same budget totals.
- [x] `[worker]` idempotent tool calls: key `hash(run_id, step, tool, arguments)`, claim before
  calling, store the result after; a resumed run gets the stored result. Decide in session (ADR
  if it is a rule) what a claimed key without a result means after a crash: retry, fail the
  run, or per-tool. Check: crash between the tool call and the checkpoint, resume, the fake
  GitHub server sees one `add_label`.
- [x] `[control-plane]` re-enqueue sweep: a scheduled job finds runs left `QUEUED` longer than a
  few minutes (saved, but the enqueue failed or was lost) and sends their message again.
  `POST /runs` already returns 503 and leaves the run `QUEUED` when enqueueing fails. Needs a
  query by status (new index or a scan). Check: a run whose enqueue failed reaches the queue.
- [x] `[control-plane]` webhook redelivery dedupe on `X-GitHub-Delivery` (conditional put, short
  TTL). Check: the same delivery twice starts one run.
- [x] `[local-stack]` chaos demo: `make chaos` kills the worker container mid-run (stub model,
  fake tools that count calls), restarts it, and checks the run succeeds with no duplicate tool
  call. **M2 done.**

## M3 tasks: approvals and Slack

Work in this order. A `require_approval` tool pauses the run (the worker already reports
`WAITING_APPROVAL` with the pending call); a person approves or rejects; approved runs resume and
make the call exactly once, rejected runs fail with `approval_rejected` (ADR 0002).

- [x] `[contracts]` approval decision in the internal API: `GET /internal/runs/{id}` returns
  `approval` (`step`, `tool`, `arguments`, `decision`, `decided_by`, `decided_at`) once a
  pending call was decided; failure reason `approval_expired`. Check: contracts tests pass;
  listed under "Cross-service changes" for control-plane and worker.
- [x] `[control-plane]` approval decisions: store the pending call when a run reports
  `WAITING_APPROVAL`; `POST /runs/{id}/approval` (API key; web approval for development and
  tests) records the decision once (conditional), approved moves the run to `QUEUED` and
  enqueues it (the sweep covers a failed enqueue), rejected moves it to `FAILED`
  `approval_rejected`; the internal GET returns the decision. Check: integration tests,
  responses validated against the contract.
- [x] `[worker]` resume after approval: a resumed run whose pending `require_approval` call
  matches an approved decision (step, tool, arguments) makes the call (idempotency key as
  usual) and continues; no or non-matching decision pauses again. Check: integration test
  pause, approve, resume runs the call once; a second resume does not repeat it.
- [x] `[worker]` `github.close_issue` in `tools_local.py` (reason `completed`, `not_planned` or
  `duplicate`; repo allowlist; idempotent: closing a closed issue changes nothing). Check:
  recorded-response tests.
- [x] `[control-plane]` Slack `ApprovalChannel`: when a run reports `WAITING_APPROVAL`, post a
  Block Kit message with the call and Approve / Reject buttons (`SLACK_BOT_TOKEN`,
  `SLACK_APPROVAL_CHANNEL`). A failed post does not fail the worker's PATCH; the sweep posts
  approvals not yet sent. Check: unit tests with recorded Slack responses; a message appears in
  a sandbox channel.
- [x] `[control-plane]` `POST /approvals/slack`: verify the signing secret (v0, 5-minute window),
  read the button click, apply the same decision path as the web approval, update the message
  to show who decided. Answers within Slack's 3 seconds. Check: Slack's documented signature
  example; a real click in the sandbox through smee.io.
- [x] `[control-plane]` approval expiry: the sweep fails runs left `WAITING_APPROVAL` longer than
  `APPROVAL_TIMEOUT_HOURS` (default 72; must stay under the worker's 15-day run-state retention,
  ADR 0011) with `approval_expired`. Check: unit tests.
- [x] `[specs]` issue-triage closes confirmed duplicates with `github.close_issue` under
  `require_approval`. Check: a planted duplicate in the sandbox pauses the run, Approve in Slack
  closes it as duplicate, Reject leaves it open and fails the run. **M3 done.**

## M4 tasks: tool gateway

Tool calls move out of the worker into `tool-gateway/` (ADR 0006): the worker holds no
credential, every call carries a signed run token (ADR 0003), and connectors are an OpenAPI file
plus an allowlist with rules and named hooks (ADR 0014). Decided when planning (2026-11-18):

- Team bot (`acts_as: team-bot`) is a GitHub App (installation tokens, actions show as
  `<app>[bot]`); `acts_as: owner` uses per-user OAuth, also in M4. No personal token in the
  gateway.
- Run token: ES256 JWT, a new one on each `GET /internal/runs/{id}` (each attempt), valid for the
  run's `max_seconds` from issue; keys from env locally, KMS signing in M6.
- Second approval check reads the existing `GET /internal/runs/{id}`; no new endpoint.
- GitHub is the only connector in M4; tool names stay (`github.search_issues`, ...).
- `TokenStore` on AWS is SSM Parameter Store SecureString (free tier), built in M6; M4 uses the
  in-memory adapter.
- Every gateway call writes an access log line: run, tenant, agent and version, who started the
  run (`started_by`), the identity it acts as, tool, argument hash, outcome; never a secret.

Work in this order:

- [x] `[contracts]` `run-token.schema.json` (claims `iss`, `aud`, `sub` = run id, `tenant`,
  `agent`, `agent_version`, `acts_as`, `owner`, `started_by`, `tools`, `require_approval`,
  `iat`, `exp`; ES256), `connector-allowlist.schema.json` (tool name to `operationId`, argument
  rules, result fields kept, hook name, annotations), and the MCP call conventions: run token as
  `Authorization: Bearer`, `step` and idempotency key in the call's `_meta`, `readOnlyHint` /
  `idempotentHint` as the tool's `read_only` / `idempotent`, tool failures as MCP tool errors
  (`isError`). Check: contracts tests (valid and invalid examples); cross-service entries for
  control-plane, tool-gateway and worker.
- [x] `[control-plane]` record who published each version (`owner`) and who started each run
  (`started_by`: `webhook:github` or `api:<key>`); sign a run token on every
  `GET /internal/runs/{id}` (`RUN_TOKEN_PRIVATE_KEY`). Tokens are never logged. Check:
  integration test verifies the token with the public key and validates its claims against the
  contract.
- [x] `[tool-gateway]` scaffold: uv project, FastAPI plus the MCP Python SDK, stateless
  Streamable HTTP with JSON responses at `/mcp`; run token checked on every request (signature,
  audience, expiry); `tools/list` shows only the token's tools, `tools/call` refuses others;
  access log; `TokenStore` port with an in-memory adapter and contract suite; a fake connector.
  `AGENTS.md` and the Commands table. Check: tests including a forged token, an expired one, and
  a tool not in the token.
- [x] `[tool-gateway]` OpenAPI-to-MCP generator: `connectors/github/` (`openapi.yaml` trimmed
  from GitHub's official description, `allowlist.yaml`) becomes MCP tools (name, description,
  input schema from parameters and body, annotations); the executor builds and sends requests;
  declarative rules: allowed repos, search scope (`repo:` added, `repo:`/`org:`/`user:` refused),
  enum limits, result fields kept (no issue bodies). Check: unit tests with recorded GitHub
  responses for each rule.
- [x] `[tool-gateway]` GitHub hooks (ADR 0014): existing labels only (listed in the tool
  description, checked on the call), `close_issue` reads first (pull requests refused, an
  already closed issue not closed again), `duplicate_of` looked up to the issue id (same repo,
  not itself); retries, rate limits and error messages without the token, as in `tools_local.py`.
  Check: every case in `worker/tests/unit/test_tools_local.py` has a passing gateway equivalent.
- [x] `[tool-gateway]` team bot: GitHub App credentials (app id and private key in `TokenStore`;
  app JWT exchanged for an installation token, cached until 5 minutes before its 1-hour expiry).
  The user creates the App (Issues read and write, Metadata read) and installs it on the sandbox
  repo. Check: recorded-response tests; a real call in the sandbox shows as `<app>[bot]`.
- [x] `[tool-gateway]` second approval check: a `require_approval` tool runs only if the run's
  `approval` (from `GET /internal/runs/{id}`) is approved for the same step, tool and canonical
  arguments; otherwise a tool error. Check: unit tests with a fake control plane (no approval,
  rejected, other step, other arguments, match).
- [x] `[worker]` `tools_mcp.py`: MCP client with the run token, `_meta` step and idempotency key,
  annotations to `ToolSpec` flags, tool errors to `ToolError`; `TOOLS_PROVIDER=mcp`,
  `TOOL_GATEWAY_URL`. Check: the `ToolClient` contract suite passes against the gateway with the
  fake connector.
- [x] `[local-stack]` gateway in compose; local run-token key pair generated by `make` (never
  committed); the GitHub App `.pem` mounted read-only from `GITHUB_APP_PRIVATE_KEY_FILE`;
  the gateway's `CONTROL_PLANE_URL` set (else approval tools are refused); `make demo` runs its tools through the gateway. Check: `make up && make demo` and
  `make chaos` pass.
- [x] `[tool-gateway]` per-user OAuth for `acts_as: owner`: through the team bot's GitHub App
  (ADR 0015, not a separate OAuth App), `/oauth/github/start`
  and `/oauth/github/callback` (state and PKCE), token stored per tenant, user and provider; an
  owner run without a stored token fails naming the login URL. Check: tests with a fake GitHub
  OAuth server; a real login through the browser to localhost; an owner run labels as the user.
- [x] `[local-stack]` the Docker worker gets named settings instead of `env_file: .env` (today its
  container holds every secret in `.env`, including `GITHUB_TOKEN` and the gateway's OAuth
  secrets, though it uses none of them; ADR 0006). Check: `docker compose config` shows no secret
  in the worker; `make demo` and `make chaos` pass.
- [x] `[local-stack]` the Docker control plane gets only its own settings instead of
  `env_file: .env` (today it also holds `GITHUB_TOKEN`, the model key and the gateway's OAuth
  secrets); list them from `control-plane/`'s configuration, then filter like `worker-env.sh`.
  Check: no other service's secret value in the container; `make demo` passes.
- [x] `[specs]` issue-triage through the gateway: `TOOLS_PROVIDER=mcp`, no `GITHUB_TOKEN` in the
  worker's environment. Check: a planted duplicate is labeled and, after Approve in Slack, closed
  by `<app>[bot]`; the gateway access log shows both identities. **M4 done.**

## M5 tasks: evals

Evals measure an agent's decisions (its spec, prompt, model and the tool descriptions it reads),
not the platform: platform correctness is covered by tests, `make demo` and `make chaos`, and
performance by M7 on AWS. Decided when planning (2026-11-18):

- One general harness for any spec: an agent's tasks live in `evals/tasks/<spec name>/`, no code.
  issue-triage is the first user; a second, different spec proves the runner needs no change.
- Runs on the host in one process, no Docker: the worker's `handle_run` with in-memory adapters
  and the spec's real model; tools through the real gateway (subprocess, as in the worker's
  tests) pointed at simulated services. Never a real outside API.
- Simulated services per connector, shared by every agent using it. GitHub: issues, labels,
  search by words, labeling and closing change the state. A connector without a simulator uses
  scripted responses matched by tool and arguments.
- Check kinds: tool called or not (argument patterns), simulated end state, final output
  pattern, budget, LLM judge on free text against a rubric the agent's author writes. Approval
  requests are approved automatically; checks decide whether they should have happened.
- Each task runs 3 times and passes on 2 of 3. Regression: a task that passes in the baseline now
  fails, or the pass rate drops more than 5 points. The baseline is a committed file per spec.
- Judge model `minimax/MiniMax-M3`. Its score is reported but not counted until it agrees with
  about 50 labels at least 90% of the time (changed when building it: the labels were made by
  Claude at the user's request, not by hand; the user may change any of them).
- The task format lives in `evals/` (only evals reads it), not `contracts/`.
- Cost about $0.005 per issue-triage run: 30 tasks x 3 runs is about $0.45.
- Later, not M5: a CI gate on pull requests (needs a GitHub secret and working through PRs), and
  tenant-run evals before a version goes live (product change, after users and RBAC).

Work in this order:

- [x] `[tool-gateway]` a connector's base URL from a setting (`CONNECTOR_<NAME>_BASE_URL`), so
  evals and tests can point a real connector at a simulator. Startup logs the override; the
  docs warn that real credentials would be sent there. Check: unit tests (override used, unset
  keeps the allowlist's `base_url`).
- [x] `[evals]` scaffold: uv project (`AGENTS.md`, `CLAUDE.md`), task format as a JSON Schema in
  `evals/` with valid and invalid examples, GitHub simulator (also answers the App token exchange
  with throwaway credentials), runner for one task, deterministic checks. Draft an ADR: agent
  evals run the worker's code in process (evals imports `worker`; judge calls go through the
  worker's model adapters, so no second provider layer). Check: tests with `ScriptedModel` pass
  a good task and fail a bad one for each check kind; one real task run by hand.
- [x] `[evals]` about 30 issue-triage tasks: plain bug, enhancement, question and docs issues;
  true duplicates worded differently; near-misses (same topic, other problem); ambiguous label;
  duplicate of a closed issue; prompt injection; non-English; a missing label. The user reviews
  them. Check: all validate; a real run of each passes at least once or is explained.
- [x] `[evals]` suite run: 3 runs per task, report (per task and per check kind, cost),
  comparison with the committed baseline, `make eval` (`SPEC=<name>` for one spec) and a way to
  save a new baseline. Check: an unchanged spec shows no regression on a rerun; a weakened
  prompt (near-miss guidance removed) shows one, naming the tasks.
- [x] `[evals]` judge: rubric per spec, a labels file where the user marks about 50 reasons good
  or bad, agreement reported. Check: agreement printed; counted only at 90% or more.
- [x] `[specs]` second agent `needs-info` (labels a bug report `needs-info` when it lacks steps to
  reproduce; other tools and output than issue-triage) with about 8 tasks in
  `evals/tasks/needs-info/`. Check: `make eval SPEC=needs-info` runs with no change to the runner.
  **M5 done.**

## M6 tasks: AWS deploy

The platform of M0 to M4 moves to AWS with no hourly cost while idle (ADR 0007), plus tracing and
protection; one live triage at the end proves all of it. Decided when planning (2026-11-19):

- Account and region: the AWS CLI profile `default`, `us-east-1`. Never the `prod` profile;
  credentials stay in `~/.aws`, never in `.env` or Terraform files.
- Terraform from the user's laptop (`terraform apply`); state in an S3 bucket with Terraform's own
  lock file (no lock table), made once by `infra/bootstrap/`. One environment, `infra/envs/dev/`.
  Deploying from GitHub Actions comes later with the CI gate.
- Every resource carries the tag `project = agent-platform` (provider `default_tags`). Budgets:
  $10 a month on that tag (the user activates it under Billing, Cost allocation tags; it counts
  from activation) and $90 a month for the whole account as a safety net (other resources run in
  it). The first two budgets are free.
- Secrets in SSM Parameter Store SecureString (free), not Secrets Manager; the run-token key in
  KMS (signing happens in KMS, the private key never leaves it). Fixed idle cost about $1 a month.
- Domain: none. Changed during `[infra]` compute (ADR 0021): the API's default `execute-api`
  address, which stays the same across deploys; the experiment is deleted at the end, so a
  custom domain in Cloudflare is not worth its setup.
- One tenant, one user. Multi-tenant use (other people, users and roles) is a later milestone.
- The control plane's sweeps run from EventBridge Scheduler every 5 minutes (configurable; within
  the free tiers, so $0 idle, though never completely silent).
- Tracing (OpenTelemetry to X-Ray: timings, tokens, cost, never issue text or model replies) and
  protection (API Gateway throttling, a daily model spend alarm) are part of M6 and come before
  the final live check, so one live run proves everything.
- First risk to settle: Spring Boot 4.1 on Lambda with SnapStart, and its cold start against
  Slack's 3-second limit (open question).

Work in this order:

- [x] `[infra]` bootstrap: `infra/bootstrap/` (state bucket: versioned, encrypted, public access
  blocked) and `infra/envs/dev/` (provider with profile `default`, `us-east-1`, `default_tags`);
  the two budgets with email alerts; `infra/AGENTS.md`, `CLAUDE.md` and the Commands table.
  Check: `terraform plan` clean after apply; both budgets visible; the user has activated the tag.
- [x] `[control-plane]` Lambda packaging trial: run the control plane on Lambda (Java 21,
  SnapStart through a published version or alias) with the adapter the trial picks for Spring Boot
  4.1; measure cold and warm response times; an entry the scheduler calls for the sweeps
  (`@Scheduled` stays for local runs). ADR 0017 records the choice. Check: numbers in the ADR;
  a cold Slack callback answers within 3 s, or the ADR says what is done instead.
- [x] `[infra]` data: both DynamoDB tables from their JSON definitions (with TTL), SQS run queue
  and dead-letter queue, ECR repositories, the KMS signing key (ECC_NIST_P256), SSM parameters
  (values put by the user, never in Terraform files). Check: apply; tables match the local ones.
- [x] `[control-plane]` AWS adapters: run tokens signed with KMS (ES256, JOSE signature from
  KMS's DER), secrets read from SSM. Check: tests with a fake KMS; a token verifies with the
  public key and validates against `run-token.schema.json`.
- [x] `[tool-gateway]` Lambda entry, SSM `TokenStore` adapter (passes the contract suite),
  SigV4 `signer` for `HttpControlPlane`, run-token public key from KMS. Check: tests; the
  gateway answers `/health` on Lambda.
- [x] `[worker]` SigV4 `auth` for `GatewayTools` (like `sigv4_signer`); image for Fargate; the
  model key from SSM through the ECS task definition. Check: tests; image runs locally.
- [x] `[tool-gateway]` read the run token from `X-Run-Token` (contract change, see Cross-service
  changes), `Authorization: Bearer` as a fallback while unsigned workers send both. Check: tests
  with both headers; `make demo`.
- [x] `[infra]` compute: Lambdas (control plane alias with SnapStart, gateway), one HTTP API
  (public routes: agents API, webhooks, Slack; IAM-authorized routes: `/internal/*`, `/mcp`) on
  its default endpoint (ADR 0021, no custom domain); Fargate service in public subnets (no NAT)
  scaled 0..N on queue depth; EventBridge Scheduler for the sweeps; least-privilege IAM roles.
  Check: publish the example spec and run it with the stub model on AWS (like `make demo`) to
  `SUCCEEDED`; workers back to 0.
- [x] `[control-plane]` tracing: OpenTelemetry spans, trace id passed to the run (queue message
  and run record; if a contract changes, list it under Cross-service changes). Changed during the
  task (ADR 0022): no upload from Lambda and no active tracing (measured: sampled-only, nothing
  after a restore, +250 ms cold start). Check: tests; the run's trace id reaches the worker
  (`make demo`).
- [x] `[worker]` tracing: continue the run's trace from the SQS `traceparent` attribute, spans per
  model call, tool call and checkpoint with the attributes in `docs/architecture.md` →
  Observability, uploaded straight to the X-Ray OTLP endpoint with SigV4 (no collector; ADR 0022).
  Transaction Search confirmed (`CloudWatchLogs` / `ACTIVE`). Checked: tests; a live span from the
  laptop found in `aws/spans`; `make demo`. The stub run's whole trace on AWS moves to the infra
  task below (needs the task definition and IAM changes).
- [x] `[infra]` worker traces: task definition `TRACING_EXPORTER=xray`; worker task role
  `xray:PutTraceSegments` and `xray:PutSpans` (AWS docs name both for the OTLP endpoint); 14-day
  retention on `aws/spans` (Transaction Search made it, so import or manage only its retention);
  push a new worker image tag; rebuild the gateway zip first (`lambda_zip.py`, now with bytecode)
  so the same apply deploys it. Check: `aws_demo.py` run's `trace_id` shows one trace in X-Ray with
  "run.start" (control plane, not uploaded, so the root is missing) and the worker's spans.
  Done: `aws/spans` imported (14 days, `skip_destroy`); `aws_demo.py` now finds the trace (11
  worker spans for the stub run).
- [x] `[tool-gateway]` tracing: read `traceparent`, span per tool call joined to the worker's
  trace. The gateway is on Lambda too: uploading follows ADR 0022 (none unless a trial shows an
  exporter that keeps responses fast). Check: tests; the trace id reaches the gateway. Done
  without upload; the access log carries `trace_id`. Lambda trial: OpenTelemetry raised the cold
  start from 3.4 s to about 3.7 s, so the zip now carries precompiled bytecode: about 2.0 s.
- [x] `[infra]` protection: route throttling, a daily model spend alarm (cost per run as a metric,
  email above a limit), log retention. Check: a burst above the limit gets 429; the alarm fires
  on a test value. Done (ADR 0023): stage default 5 per second, burst 10 (`throttle_check.py`: 6
  of 80 got 429; unsigned IAM requests are not counted); alarm on `AgentPlatform/dev`
  `ModelCostUsd` over the last 24 hours, SNS email, fired on a 2 USD test value; metric filters
  cannot read the span's `cost.usd`, so the worker writes the metric (next task);
  `/aws/application-signals/data` imported with 14 days.
- [x] `[worker]` spend metric (ADR 0023): one CloudWatch embedded metric format line per model
  call, namespace `METRICS_NAMESPACE` (unset = none), metric `ModelCostUsd` in US dollars, no
  dimensions; push a new image tag and deploy it (`worker_image_tag`). Check: tests; a real
  MiniMax call on AWS (or the stub with a nonzero test price) shows the value in CloudWatch
  (`aws cloudwatch get-metric-statistics`). Code done (`ports/metrics.py`, `EmfMetrics`; the
  stub writes 0, so a stub run shows samples); 355 tests pass. Deployed as image `05cfaf3`:
  the `aws_demo.py` stub run gave 3 samples (Sum 0) in `AgentPlatform/dev` `ModelCostUsd`, no
  dimensions.
- [x] `[specs]` go live: GitHub App key and settings into SSM, the App's webhook URL, its OAuth
  callback URL and Slack's Request URL moved to the API's `execute-api` address (no tunnels;
  `terraform output api_url`); `gateway_connectors = "github"` and `github_repos` in
  `terraform.tfvars`; a planted duplicate in the sandbox is labeled
  and, after Approve in Slack, closed by `<app>[bot]` on AWS; its trace in X-Ray; a day later the
  project tag's cost shows no hourly charge. **M6 done.** Done 2026-11-20: run
  `SUCCEEDED` from the webhook (`issues.opened`), 16 steps, $0.0076; Approve in Slack (169 ms at
  the API); issue closed `duplicate` with `bug` and `duplicate` labels, by the App's bot; 9 gateway
  calls as `team-bot`; 34 worker spans under the run's `trace_id`; `ModelCostUsd` 7 samples,
  Sum $0.007567. Steps in `specs/README.md` → Run issue-triage on AWS. Cost check done
  2026-11-21: Cost Explorer on the `project` tag shows only KMS (the key's fixed $1 a month);
  nothing billed by the hour. Worker tasks are not tagged yet (Fargate cost shows untagged;
  see Future work), which does not change the result: Fargate is $0 at 0 workers. Account-wide
  the same day: ECS $0.02 (load tests, so far), EC2-Other $0.06 from an unrelated instance's
  disk (this stack has no EBS, NAT gateway or Elastic IP), VPC $0.12 a day mostly that
  instance's public IPv4 ($0.005 an hour); worker tasks add $0.005 an hour each while running.
(The `aws_demo.py` agent-name task added here moved into M7 task 1.)

## M7 tasks: scale proof

Against the M6 deployment on AWS: cold starts, scaling from 0 and SQS behave as they do only
there. Stub model and the gateway's fake connector, never real providers or GitHub. Decided when
planning (2026-11-20):

- Load driver: a plain Python script in `loadtest/` (asyncio + httpx for the API, boto3 for logs
  and metrics), not k6 or Locust: the test starts N runs and watches them finish; it is not a
  flood of HTTP requests against one endpoint.
- Sizes in steps: 100 runs, then 1,000; each paid test only after the user says go, with an
  estimate. Budget for all of M7: $5 (estimated under $1 per 1,000-run test).
- Limits found (2026-11-20): Lambda concurrent executions 10 (raised to 1,000 on 2026-11-20;
  check with `aws service-quotas get-service-quota --service-code lambda --quota-code
  L-B99A9384`), Fargate 64 vCPU (about 128 workers at 0.5 vCPU), API throttle 5 per
  second per route, `worker_max_tasks` 2, and scale-out of one task per alarm with a 2-minute
  cooldown (20 tasks would take about 40 minutes).
- Queue-to-start without a contract change: the run's `created_at` (API) against the start of
  its first "run.attempt" span in `aws/spans`, joined by `trace_id`. Duplicate side effects: tool
  calls with the same run, step and arguments hash in the gateway access log.
- Dev is live (`gateway_connectors = "github"`, M6): load mode switches it to `fake` for the test
  and back after; the live `issue-triage` is never replaced.

Work in this order:

- [x] `[infra]` load-test mode: tfvars switches used only during a test (API throttle limits,
  `worker_max_tasks` 20, `gateway_connectors = "fake"`); scale-out sized to the backlog (step
  scaling on queue depth, more tasks for a deeper queue, instead of one per alarm);
  `aws_demo.py` publishes and runs its own agent (for example `demo-stub`), never
  `issue-triage`, and refuses with a clear message when the gateway's connectors cannot serve
  the stub's tools. Needs the Lambda quota above 10. Check: `aws_demo.py` passes in load mode;
  `plan` back to normal settings restores them; the live `issue-triage` version is unchanged.
  Done (ADR 0024): `load_test` switch (`load_test.tf`, `load_test_settings`: 200/400, 20
  workers, 2,000 ms); scale-out steps +1/+5/+10 by queue depth; `aws_demo.py` runs `demo-stub`
  and refuses without `fake`. In load mode its run `SUCCEEDED` (259 s waiting for a worker, model
  calls 2,000 ms); back to normal, `plan` shows no changes, gateway `github`; the control plane
  log shows no `issue-triage` publish after version 4. Worker image `c53f4c6` deployed. The
  Lambda quota is still 10 (request open), needed before the 100-run test.
- [x] `[worker]` stub model latency: a setting (for example `MODEL_STUB_LATENCY_MS`) that makes
  each stub model call take a fixed time, so runs last like real ones; the stub's tool calls
  stay the demo script. Check: tests; image pushed. Done: `MODEL_STUB_LATENCY_MS` (0 to 60000,
  default 0, empty = unset), `StubModel(latency_seconds, sleep)`; 366 tests pass. Image pushed
  at the commit (tag in the handoff note); not deployed: the load-mode task sets the tag and the
  value (`worker_model_env`).
- [x] `[loadtest]` driver: `loadtest/` (uv project, `AGENTS.md`): publish the load agent, start N
  runs at a set rate, poll until each is final; read first "run.attempt" start times from
  `aws/spans`, worker CPU and memory from ECS metrics; print queue-to-start p50 and p95, runs per
  minute, failures, CPU and memory. Check: 100 runs all `SUCCEEDED`, numbers printed (cost
  estimate first). Done: `uv run loadtest --runs N --rate R` publishes `load-stub` (trigger
  `manual.load.run`), refuses outside load mode; 44 unit tests. Two 100-run tests in load mode,
  both 100/100 `SUCCEEDED`, 0 redelivered, 0 retried requests. From idle (queue quiet for hours,
  workers at 0): queue to start p50 377 s, p95 383 s; created to final p50 384 s; 10 workers;
  about 230 runs per minute once workers were up; CPU avg 10% max 32%, memory 8-9%. Warm (workers
  up): queue to start p50 0.1 s, p95 1.2 s; created to final p50 6.7 s, p95 7.8 s; 347 runs per
  minute; 15 workers; CPU avg 3% max 15%, memory 9%. The idle wait is SQS, not Fargate: a queue
  inactive for over six hours stops sending metrics, and they come back up to 15 minutes after it
  wakes (AWS docs), so the scale-out alarm fired about 6 minutes after the runs were queued;
  tasks were running about 20 s later.
- [x] `[infra]` scale-out from an idle queue: an idle queue's metrics stop after six hours and
  come back up to 15 minutes after it wakes, so the first run after a quiet period waits for the
  alarm (measured about 6 minutes; also the first real issue after a quiet day). Options: keep the
  queue active (an EventBridge Scheduler call to `sqs:GetQueueAttributes` every few hours, about
  free) or start a worker on enqueue when the service is at 0 (control plane, ADR 0007 mentions
  it; a new ADR either way). Check: a run after more than six hours idle starts within about 4
  minutes (was 3: an active queue measured about 4, see below); `loadtest` from idle shows it. Applied 2026-11-20 (ADR 0025): EventBridge Scheduler
  `keep_queue_active` calls `TagQueue` on the run queue every hour with its own `project` tag
  (Scheduler refuses read-only actions such as `GetQueueAttributes`); `plan` clean, tags
  unchanged. Left: the check after a quiet night (the queue would have slept from about 04:45 UTC
  2026-11-21): its metric points keep coming every minute, and one cold run (a planted issue in
  the sandbox, live `issue-triage`, about $0.01) reaches a worker within about 4 minutes. For
  scale: with an active queue and 0 workers (chaos test, 23:18 UTC), runs queued at 23:18:00, the
  alarm fired 23:21:29, tasks ran from 23:21:57 (metric lag plus the one-minute alarm period).
  Closed 2026-11-21 without the overnight check (not needed for M7's goal): the hourly calls
  succeed (CloudTrail `TagQueue`, 22:58 and 23:57 UTC), and AWS documents that any call keeps a
  queue active. Optional follow-up, free, no run needed: after six hours without runs, the run
  queue's `ApproximateNumberOfMessagesVisible` still has a point every minute (`infra/AGENTS.md`
  → "Is the run queue reporting?"); if not, reopen with another call or a worker started on
  enqueue.
- [x] `[loadtest]` chaos: stop random worker tasks while runs are in progress (`ecs stop-task`);
  count lost runs (not final, or `FAILED` from the stop) and duplicate side effects from the
  gateway access log. Check: at 100 runs, 0 lost and 0 duplicates. Done: `--chaos-every S
  --chaos-max N` stops a random running task every S seconds while runs are open; the report adds
  tasks stopped (killed mid-run = exit 137, drained = exit 0), lost runs, and tool calls run `ok`
  more than once per idempotency key (gateway access log); takeovers counted from the run
  message's receive count (`queue.receive_count` on "run.attempt": a killed worker never uploads
  its spans). 56 tests. On AWS (2026-11-20, load mode with `stub_latency_ms = 15000` so runs
  outlast the 30 s stop timeout): 100/100 `SUCCEEDED`, 8 tasks stopped (5 killed mid-run, 3
  drained), 13 runs taken over by another worker (SQS: 100 sent, 113 received), 200 tool calls,
  0 repeated, 0 lost. Limit: the stub calls only `github.search_issues` (read-only) and
  `github.add_label` (idempotent); a non-idempotent tool (Slack post) under chaos is not covered.
- [x] `[infra]` runs per worker in load mode: `worker_concurrency` (normal 4) and
  `load_test_settings.worker_concurrency` (50: 20 tasks x 50 = up to 1,000 runs executing at once,
  instead of 80), load throttle 1,000 per second, burst 2,000. Check: normal `plan` changes no
  resource; `plan -var load_test=true` shows `WORKER_CONCURRENCY` 50. Done 2026-11-20 (ADR 0024
  amendment): both plans as expected, nothing applied (the loadtest session turns load mode on).
- [x] `[loadtest]` full test and report: 1,000 runs without and with chaos; `loadtest/report.md`
  (setup, numbers, limits hit, cost); settings back to normal (`plan` shows the dev values).
  **M7 done.** Done 2026-11-21 (the report's actual cost line waits for the M6 cost check): four 1,000-run tests at 50 runs per worker, all 1,000/1,000
  `SUCCEEDED`; up to 918 executing at once (20 workers up, 15 s model calls); chaos at 1,000 (12
  workers stopped, 6 killed mid-run): 88 runs taken over, 0 lost, one idempotent `add_label`
  repeated by design (ADR 0013), 0 duplicate side effects; 0 Lambda throttles or errors. Limits:
  scale-out from 0 about 3-4 minutes (first step can be +5, then 2-minute cooldown), 0.5 vCPU at
  100% with 50 runs of 2 s calls. Load mode off, `plan` clean.

## M8 tasks: production limits

M7 measured the platform with infinitely patient model and tools. Real use has several tenants
sharing workers and providers that answer 429. Planned 2026-11-21:

- Real tenants for the API: a tenant record holds its limits (`max_running`, `daily_usd`); per-
  tenant API keys stored hashed (`APIKEY#<hash>`, planned in the table design); a script creates a
  tenant and shows its key once; today's tenant becomes the first record. GitHub webhooks and
  Slack stay on that tenant (mapping App installations to tenants is later).
- Fairness: every run message carries `MessageGroupId` = tenant, so SQS fair queues deliver a
  quiet tenant's runs ahead of another tenant's backlog. No queue per tenant.
- Cap on runs at once, checked at start: QUEUED → RUNNING takes a slot (DynamoDB conditional
  update on the tenant's counter); at the cap the control plane re-queues the run with a delay
  (SQS `DelaySeconds`) and answers 429, and the worker drops its message (the control plane is
  the only sender, and a re-sent message does not count toward the dead-letter queue's 5
  receives). A slot is freed at the end, at WAITING_APPROVAL (can last 72 h) and when set aside;
  a sweep recounts slots in case one leaks.
- Rate limits set a run aside instead of failing it: when the model still answers 429 after the
  worker's retries, the worker calls `POST /internal/runs/{id}/defer`; the control plane moves
  the run back to QUEUED and re-queues it with the delay; after 15 minutes set aside in total the
  run fails `rate_limited`. Waiting is not working time (ADR 0012).
- Daily spend cap: the control plane adds each run's cost to a counter per tenant per day; over
  the cap `POST /runs` answers 429 (`tenant_budget`) and a queued run fails `tenant_budget` at
  start; runs already executing finish within their own budgets.
- Tests with the stub: a 429 rate and a test price per call (worker settings, set in load mode).
  AWS tests about $1-2 in all; no real model.

Work in this order:

- [x] `[contracts]` shapes: PATCH to RUNNING may answer 429 (`Deferred` problem, `Retry-After`)
  or 409 after a spend refusal; `POST /internal/runs/{run_id}/defer` (`RunDefer`: reason
  `rate_limited`, `retry_after_seconds` 1-900); failure reasons `rate_limited`, `tenant_budget`
  (control plane only); run message documents `MessageGroupId` = tenant. Check: tests. Done
  2026-11-21: 115 tests (from 96); ADR 0026 (limits at start, fair queue) and ADR 0027 (set
  runs aside on rate limits).
- [x] `[control-plane]` tenants: tenant records with limits, hashed per-tenant API keys (the
  single `TENANT_API_KEY` becomes the first tenant's key), a create-tenant script (key shown once,
  never stored in clear). Check: tests; two tenants on AWS, each sees only its own agents and
  runs. Done 2026-11-21: 355 tests; on AWS tenant B gets 404 on A's run and A's agent, makes its
  own agent of the same name, unknown keys 401 (8 checks, no runs started).
- [x] `[control-plane]` limits: slots at start (429 and delayed re-queue), `defer` (15-minute
  total, then `rate_limited`), daily spend counter (refuse `POST /runs`, fail at start), message
  group = tenant, slot recount in the sweep; `POST /runs` 429 documented in the architecture.
  Check: tests (Testcontainers DynamoDB); the contract examples pass the control plane's checks.
  Done 2026-11-21: 403 tests (from 355); the recount became a stalled-run sweep (ADR 0028). Not
  deployed yet: deploy with the worker task (today's worker does not handle the 429 at start).
- [x] `[worker]` limits: 429 at start (drop the message), `defer` on model rate limits after the
  retries (drop the message, release the lease), `approval_expired` in its failure reasons; stub
  settings `MODEL_STUB_429_RATE` and `MODEL_STUB_PRICE_USD`. Check: tests; image pushed. Done
  2026-11-21: 409 tests (from 366); image tagged with the task's commit, pushed to ECR, not
  deployed (deploy with the control plane in the infra task).
- [x] `[infra]` load mode sets the stub's 429 rate and price (in `load_test_settings`); the queue
  works as a fair queue (no setting needed; check the docs); IAM if anything new is called.
  Check: `plan` as expected in both modes. Done 2026-11-21: `stub_429_rate` 0.2 and
  `stub_price_usd` $0.0001 a call in load mode; fair queue confirmed in the SQS guide (no
  setting, no consumer change); no IAM, route or table change. Control plane (version 4) and
  worker `5ef8358` deployed in one apply; a sweep answered `"stalled":0`, no run started.
- [x] `[loadtest]` two tenants: a burst for one, a trickle for the other (start times per tenant),
  the cap held (runs executing per tenant over time), 429 injection (0 failed, runs set aside
  counted), spend cap (refusals counted); a section in `loadtest/report.md`. Check: the M8 row's
  numbers. **M8 done.** Done 2026-11-21: `uv run loadtest-limits`, three test tenants
  (`load-a` cap 100, `load-b`, `load-c` $0.005 a day); all 7 checks passed on the third run (the
  first two found the one-worker start and the spend tenant's timing; the worker got
  control-plane spans in between to bracket slots). 76 tests.

## Future work

Not started. Small tasks found along the way, then larger ideas.

- [ ] `[infra]` tag the worker tasks: the ECS service has no `propagate_tags`, so Fargate tasks
  carry no `project` tag and their cost is untagged (the $10 project budget misses it; the $90
  account budget still counts it). Set `propagate_tags = "SERVICE"` and
  `enable_ecs_managed_tags = true` on `aws_ecs_service.worker`. Check: a running task's tags
  include `project`; a day later Cost Explorer shows ECS under the tag.
- [ ] `[worker]` then `[tool-gateway]`: drop the run token's `Authorization: Bearer` copy and
  fallback (Cross-service changes).
- [ ] `[specs]` `specs/README.md`: the gotcha "do not run `aws_demo.py` while live" is outdated
  (it runs its own `demo-stub` and refuses without the fake connector).
- [ ] `[control-plane]` cold slot counter: in the first M8 run (Lambdas cold) a start PATCH took
  up to 20.8 s (p95 4.8 s) under a 1,000-run burst, warm p95 0.12 s. Measure whether a cheap read
  of `running` before the transaction (defer at once when at the cap) cuts the conflicts; only
  if it does, change it. Check: the M8 test's start-PATCH times, cold.
- [ ] `[ci]` GitHub Actions: each component's tests on push and pull request (control plane with
  Docker, the Python services, contracts, `terraform validate`), a status badge in the README;
  later an eval gate on spec changes (a model key as a GitHub secret).

Larger ideas:

- **Users and roles (RBAC):** who may publish an agent, approve a call, or publish with
  `acts_as: owner`. Needs users in the control plane (today a tenant has API keys, not users);
  the owner login link then moves behind the user's sign-in (ADR 0015: today anyone holding a
  valid link can connect their account as that owner).
- **GitHub App installations mapped to tenants**, so webhooks start runs for the right tenant
  (today every webhook runs for the first tenant).
- **Faster first start:** from 0 the workers take about 3 minutes to start with an active queue,
  about 6 after a quiet day (ADR 0025). A Lambda worker entrypoint for short runs (ADR 0005)
  would remove the wait.
- **Upstream fixes:** when the platform hits a real bug in a library it uses (MCP Python SDK,
  OpenTelemetry, the Lambda Web Adapter), send the fix upstream.

## Cost guardrails

- Set an AWS Budgets alert before deploying anything, and a spend limit or alert on every model
  provider account (MiniMax, OpenRouter, ...).
- Nothing billed per hour (ADR 0007): no NAT gateway, load balancer, RDS, interface VPC endpoints
  or always-on instance. Public subnets for Fargate tasks; Lambdas outside the VPC.
- Before sharing the URL with others: API Gateway route throttling, per-run budgets enforced, and a
  daily model spend alarm (all three in place once the worker writes its spend metric, ADR 0023).
  Throttling is soft; the account's Lambda concurrency (1,000) is the real ceiling.
- Use the cheapest capable model for evals; use recorded fixtures so evals never call real
  APIs.
- Workers scale to 0 when idle; run load tests only against the stub model.

## Answered questions

- Public repo: yes; personal and account values stay in git-ignored files (AGENTS.md, infra).
- Spring Boot on Lambda fast enough for Slack's 3-second callback: yes with SnapStart and
  priming, a cold click in about 2.2 s (ADR 0017).
- Scale-from-0 delay for demos: accepted (about 3 minutes with an active queue, about 6 after a
  quiet day; the hourly queue keep-alive of ADR 0025 is meant to keep it near 3, not checked
  overnight); a Lambda worker is future work.

## Cross-service changes

Changes to `contracts/` that another service still has to adopt. Remove an entry once every listed
service has followed.

- **The run token moves from `Authorization: Bearer` to the `X-Run-Token` header** (2026-11-19;
  on AWS `Authorization` carries the SigV4 signature API Gateway checks on `/mcp`). Declared in
  `contracts/mcp-tool-call.schema.json` and `internal-api.openapi.yaml` (`run_token`). `worker`
  done: always sends `X-Run-Token`, and `Authorization: Bearer` too when unsigned. `tool-gateway`
  done: reads `X-Run-Token` (alone when present), Bearer only as a fallback. Must follow, in any
  later session: `worker` drops the Bearer copy (`tools_mcp._run`), then `tool-gateway` drops the
  fallback (`app._run_token`) and its test; then this entry goes.

## Handoff notes

Newest first. One entry per work session: date, component, done, next, gotchas. Entries before
M8 were trimmed when the project closed; they are in git history.

- **2026-11-21 · project closed** — README rewritten for visitors (what it is, measured results,
  architecture, design decisions, how to run it); this roadmap condensed (status per milestone,
  future work, answered questions, handoff notes from M8 only); `specs/README.md`'s outdated
  `aws_demo.py` gotcha fixed. State left on AWS: dev deployed with load mode off, test tenants
  `load-a`, `load-b`, `load-c` on the control-plane table (keys in `~/.config/agent-platform/`).
  To pick it up again: "Future work" above.
- **2026-11-21 · loadtest** — M8 task 6 done, **M8 done**. `loadtest-limits` (`limits.py`,
  `limits_cli.py`): warm-up of 300 runs until 8 workers, then `load-a` 1,000 runs at 50/s (cap
  100), `load-b` 1/s for 180 s, `load-c` one run at a time until refused; numbers from spans
  (slot bracket from the worker's `control_plane.*` spans, put-off and set-aside attempts), the
  queue's quiet-tenant metric. `api.py` no longer retries a `tenant_budget` 429. Run 3 passed
  all 7 checks (`report.md` → M8). Found on the way: with 1 worker the fair queue did not keep
  the quiet tenant fast (p50 110 s; capacity, not fairness); cold, a start waits on the busy
  slot counter up to 20.8 s (follow-up); attempt spans overcount runs at once. Tenants `load-a`,
  `load-b`, `load-c` stay on the table; keys in `~/.config/agent-platform/` (0600). Load mode
  off, `plan` clean. Next: plan the next milestone or the follow-ups. Gotchas: `load-c`'s spend
  counter resets at 00:00 UTC (raise its cap to test again the same day); the stub's fake spend
  can trip the $1 spend alarm.
- **2026-11-21 · worker** — Spans around control-plane calls (for M8 task 6, not a roadmap
  task): the first M8 load test could not tell a start waiting on the busy slot counter (up to
  20.8 s under a 1,000-run burst) from time holding a slot, and control-plane spans are not
  uploaded. `handle_run` now wraps every `update_run` / `defer_run` in `control_plane.update`
  (`run.status` asked) / `control_plane.defer` (`run.status` answered), `outcome` ok, deferred,
  conflict or error. 413 tests. Next: image, apply (tag only, load mode on), rerun the M8 test.
- **2026-11-21 · infra** — M8 task 5 done. `load_test_settings` has seven values now:
  `stub_429_rate` (0.2) and `stub_price_usd` ($0.0001 a call) become the worker's
  `MODEL_STUB_429_RATE` / `MODEL_STUB_PRICE_USD` in load mode (`load_test.tf`). The run queue is
  a fair queue with no setting (SQS developer guide; quiet-tenant metrics such as
  `ApproximateNumberOfMessagesVisibleInQuietGroups`). No IAM change: `/defer` falls under
  `ANY /internal/{proxy+}` and the worker's `execute-api` wildcard; the control plane already had
  `TransactWriteItems`. Deployed: control-plane version 4, worker image `5ef8358` (task definition
  revision 14); `plan` is clean, load mode off. The tenant-limits cross-service entry is closed.
  Next: `[loadtest]` two tenants (burst and trickle, cap held, 429 injection, spend cap). Gotchas:
  the stub price goes into the real `ModelCostUsd`, so keep price x calls a day under the $1 alarm
  (the spend-cap test needs a tenant with a tiny daily cap, not a higher price); the sweep result
  is returned, not logged (invoke it: `infra/AGENTS.md`); public routes take the key in
  `X-Api-Key`, not `Authorization`.
- **2026-11-21 · worker** — M8 task 4 done: limits. New core errors `RunDeferred` (PATCH to
  RUNNING answered 429 with the `Deferred` problem type; `HttpControlPlane` does not retry it,
  other 429s still are) and `ModelRateLimited(ModelError)` (OpenAI-compatible: last try 429 or
  MiniMax codes 1002, 1039, 1041, 1045, 2045-2047; Bedrock: `ThrottlingException`). `handle_run`:
  deferred start returns QUEUED; a 409 at start re-reads the run (FAILED `tenant_budget`);
  `ModelRateLimited` reports progress (RUNNING, steps, cost), then `defer_run` (provider
  Retry-After clamped 1-900, else 30 s ± 20%, `set_aside_delay`) and returns its answer. Port
  `ControlPlane.defer_run`; `InMemoryControlPlane(max_running=...)`, `over_budget`, 15-minute
  total; the fake HTTP server serves `/defer` and the 429. `FailureReason` matches the contract
  enum. Stub: `MODEL_STUB_429_RATE`, `MODEL_STUB_PRICE_USD` (per call, through the `stub/*` price
  in `Deps.models`). Next: `[infra]` load-mode settings and deploy control plane + worker
  together (worker image first or at the same apply: an old worker retries the 429 three times,
  then keeps the message). Gotchas: `make worker-image` tags with HEAD, so build after the commit;
  a stub run set aside resumes at the same script position (counted from assistant messages).
- **2026-11-21 · control-plane** — M8 task 3 done: limits. `RunStore` reads a run, then writes
  on the condition that status, cost and `holdsSlot` are unchanged (retried, backoff, 10 tries);
  QUEUED → RUNNING and leaving RUNNING are one transaction with `TENANT#<t>/USAGE` (`running`,
  taken only below `max_running`). Cost increases `ADD` to `TENANT#<t>/SPEND#<day>` (UTC, ttl 35
  days). `RunService.update`: a start over the daily cap fails `tenant_budget` (409); at the cap
  `TenantAtCapException` → touch queued-at to now + delay, re-send with the delay, 429 `Deferred` +
  `Retry-After` (`RunLimits`: `TENANT_CAP_RETRY_SECONDS` 30 ± 20%). `POST /internal/runs/{id}/defer`
  (`RunService.defer`, `RunStore.setAside`; 15 min total → `rate_limited`). `POST /runs` and
  webhooks: 429 `tenant_budget`. Publisher: `MessageGroupId` = tenant, `publish(message, delay)`.
  Decision (ADR 0028): no recount (the counter cannot drift); a stalled-run sweep fails runs
  RUNNING with no update for 90 min (`SWEEP_RUNNING_STALLED_AFTER_SECONDS`), index
  `RUNS#RUNNING#<0-7>`. 403 tests. Next: `[worker]` limits, then deploy both together. Gotchas:
  the USAGE item is hot under a burst (transaction conflicts are retried; the M8 load test will
  show if 10 tries are enough); ElasticMQ 1.7.1 accepts `MessageGroupId` on a standard queue;
  Lambda sweep output now has `stalled`.

- **2026-11-21 · control-plane** — M8 task 2 done: tenants. `TenantStore` port (DynamoDB,
  in-memory, contract suite): `TENANT#<t>` record with `maxRunning` and `dailyUsd`;
  `APIKEY#<sha256>` → tenant, added in a transaction that checks the tenant exists. `ApiKeys`
  makes `ap_` + 32 random bytes (base64url). `ApiKeyAuthFilter`: configured `TENANT_API_KEY`
  first (decision: it stays the first tenant's key, no record needed; limits from
  `TENANT_MAX_RUNNING`/`TENANT_DAILY_USD`), then stored keys by hash; keys not shaped `ap_…` never
  read the table. `TenantDirectory.limits(t)` for the next task. `TenantAdmin` CLI
  (`./gradlew tenantAdmin --args="create <t> --table <table>"`, also `add-key`, `set-limits`,
  `show`) prints a key once. Priming sends no API key when none is held (it used to send a fake
  one, which made the filter read SSM before the snapshot). 355 tests. On AWS: `tenant-b`
  created (key outside the repo), isolation checked with 8 requests. Next: `[control-plane]`
  limits (slots, `defer`, spend counter, `MessageGroupId`), using `TenantDirectory`. Gotchas: the
  dev gateway runs only `github`, so an isolation check must not start stub runs (they would
  call real GitHub tools); reuse a load-test run id instead. The AWS SDK warns about profile
  names in `~/.aws/config` without `profile `; harmless.

- **2026-11-21 · contracts** — M8 planned (row, decisions, tasks above) and task 1 done. The
  internal API: PATCH to RUNNING may answer 429 (`Deferred` response: `DeferredProblem` with
  `reason` `tenant_running_limit` and `retry_after_seconds`, header `Retry-After`) or 409 after a
  spend refusal; new `POST /internal/runs/{run_id}/defer` (`RunDefer`); failure reasons
  `rate_limited` and `tenant_budget`; the run-state description covers slots and setting aside.
  Run message: `MessageGroupId` = tenant (description). Examples `internal-run-defer.*`,
  `deferred-problem.valid.json`; 115 tests. Cross-service entry added. Next: `[control-plane]`
  tenants. Gotchas: the public API (`POST /runs`, its 429) has no contract file: the control
  plane documents it; delays are capped at 900 s by SQS.
