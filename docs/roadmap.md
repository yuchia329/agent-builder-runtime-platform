# Roadmap

Current status, milestones and next tasks. Update this file as work moves; the other docs describe
the target design and should change only when the design does.

**Status:** M1 done (MVP): `specs/issue-triage.yaml` runs on real MiniMax (`minimax/MiniMax-M3`)
with real GitHub tools (`TOOLS_PROVIDER=local`) through the local stack, labels a new issue and
flags a planted duplicate (steps in `specs/README.md`). M0 `make up && make demo` still runs the
stub spec. Opening an issue in the sandbox starts a run by itself (`POST /triggers/github`, signed
webhook delivered locally through smee.io). Bedrock also works (`bedrock/amazon.nova-lite-v1:0`
through the Converse adapter), though Nova Lite triages worse than MiniMax. **M2 done (crash
safety):** lease, per-step checkpoints, idempotent tool calls (ADR 0011-0013), re-enqueue sweep,
webhook dedupe; `make chaos` kills the worker mid-run with SIGKILL and the run resumes and adds
its label exactly once. **M3 done (approvals):** issue-triage closes a high-confidence duplicate
only after Approve in Slack (Reject fails the run with `approval_rejected`, no decision in 72 h
with `approval_expired`); checked live in the sandbox. **M4 done (tool gateway):** every tool call
goes through the tool gateway over MCP with a signed run token; the GitHub connector is generated
from GitHub's OpenAPI description; issue-triage labels and (after Approve) closes a duplicate as
the GitHub App's bot (`acts_as: team-bot`), and `acts_as: owner` works through a user login
(ADR 0015); no other container holds a GitHub credential. **M5 done (evals):** `make eval` scores every spec with
tasks in `evals/tasks/` on its real model against a simulated GitHub through the real gateway (ADR
0016), 3 runs per task, compared with a committed baseline; an LLM judge (trusted at 99-100%
agreement with 75 labels) grades issue-triage's answers. Baselines: issue-triage 29/30 tasks,
needs-info 8/8. **M6 (AWS deploy) in progress:** Terraform bootstrap applied (state bucket, two
budgets); control plane packaged for Lambda (ADR 0017: SnapStart with priming, 1769 MB arm64, a
cold Slack click in about 2.2 s); dev data applied (tables, run queue, worker ECR, KMS signing key,
SSM secret parameters; ADR 0018); control plane signs run tokens with KMS and reads secrets from SSM
(`SECRETS_SOURCE=ssm`); gateway runs on Lambda with the Web Adapter (ADR 0019), SSM `TokenStore`,
KMS public key, SigV4 to the control plane; worker signs gateway calls (run token moved to
`X-Run-Token`, which the gateway reads; ADR 0020), arm64 Fargate image; dev compute applied (both
Lambdas behind one HTTP API on its default endpoint, ADR 0021; workers on Fargate scaled 0..2 on
queue depth): the stub demo runs to `SUCCEEDED` on AWS and the workers return to 0; the control
plane starts each run's trace and passes it on the queue message (ADR 0022; it uploads no spans
from Lambda); the worker continues it (span per attempt, model call, tool call, checkpoint) and
can upload straight to X-Ray (`TRACING_EXPORTER=xray`, checked live); the gateway joins it (spans
made, not uploaded; its Lambda zip now carries bytecode, cold start about 2.0 s from 3.4 s); all
three deployed, and a stub run on AWS shows its worker spans in X-Ray under the run's `trace_id`
(`aws_demo.py` checks it); protection applied (ADR 0023): routes throttled (5 per second, burst
10; best-effort, a test stream got 429s), a model spend alarm emails above 1 USD in 24 hours (fired
on a test value), every log group 14 days; the worker writes `ModelCostUsd` per model call
(deployed, seen in CloudWatch). **Live on AWS (2026-11-20):** a planted duplicate in the sandbox
started a run through the App's webhook, was labeled, and after Approve in Slack was closed by
`<app>[bot]` (16 steps, $0.0076, its trace and spend metric in CloudWatch); M6 is done once the
project tag's cost shows no hourly charge (tag activated 2026-11-20, readable within 24 h).
**M8 (production limits) in progress:** planned 2026-11-21; contracts and control-plane tenants
done; next `[control-plane]` limits.
**M6 done (2026-11-21):** the cost check shows only the KMS key's fixed $1 a month, nothing
billed by the hour. **M7 (scale proof) done:** load-test mode (ADR 0024) and the load driver (`loadtest/`)
done; 100 stub runs all `SUCCEEDED` on AWS, from idle (about 6 minutes to start, an idle SQS
queue's metrics; an hourly keep-alive applied, ADR 0025) and warm (start p50 0.1 s, 347 runs per
minute); chaos at 100 runs (8 workers stopped, 5 killed mid-run, 13 runs taken over): 0 lost,
0 repeated tool calls. 1,000-run tests at 50 runs per worker: all `SUCCEEDED`, up to 918
executing at once; chaos at 1,000 (12 stopped, 88 taken over): 0 lost, 0 duplicate side effects
(`loadtest/report.md`). **M7 done (2026-11-21).**

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
| M2  | Crash safety   | Lease, checkpoints, idempotency; killing a worker mid-run causes no duplicate tool call                                                                       | Done        |
| M3  | Approvals      | Close-issue pauses; Slack buttons resume or reject the run                                                                                                    | Done        |
| M4  | Gateway        | Tool calls move into the gateway; OpenAPI → MCP generator; OAuth and team bots                                                                                | Done        |
| M5  | Evals          | `make eval` scores any spec that has eval tasks (real model, simulated services) and flags a regression; issue-triage and a second spec                        | Done        |
| M6  | AWS deploy     | Terraform deploys everything scale-to-zero (ADR 0007): Lambdas behind one API Gateway HTTP API, workers at 0; traces in X-Ray; no hourly cost when idle | Done        |
| M7  | Scale proof    | 1,000 concurrent runs on the stub model against the AWS deployment; chaos test report in `loadtest/report.md`                                                 | Done        |
| M8  | Production limits | Two tenants on AWS: while tenant A starts 1,000 runs at once and tenant B 1 a second, B's runs start within 30 s (p95, workers up) and A never exceeds its cap on runs at once; with 20% of stub model calls answering 429 no run fails; a tenant over its daily spend cap gets new runs refused; numbers in `loadtest/report.md` | In progress |

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
  see Follow-ups), which does not change the result: Fargate is $0 at 0 workers. Account-wide
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
- [ ] `[control-plane]` limits: slots at start (429 and delayed re-queue), `defer` (15-minute
  total, then `rate_limited`), daily spend counter (refuse `POST /runs`, fail at start), message
  group = tenant, slot recount in the sweep; `POST /runs` 429 documented in the architecture.
  Check: tests (Testcontainers DynamoDB); the contract examples pass the control plane's checks.
- [ ] `[worker]` limits: 429 at start (drop the message), `defer` on model rate limits after the
  retries (drop the message, release the lease), `approval_expired` in its failure reasons; stub
  settings `MODEL_STUB_429_RATE` and `MODEL_STUB_PRICE_USD`. Check: tests; image pushed.
- [ ] `[infra]` load mode sets the stub's 429 rate and price (in `load_test_settings`); the queue
  works as a fair queue (no setting needed; check the docs); IAM if anything new is called.
  Check: `plan` as expected in both modes.
- [ ] `[loadtest]` two tenants: a burst for one, a trickle for the other (start times per tenant),
  the cap held (runs executing per tenant over time), 429 injection (0 failed, runs set aside
  counted), spend cap (refusals counted); a section in `loadtest/report.md`. Check: the M8 row's
  numbers. **M8 done.**

## Follow-ups

Small tasks found along the way, outside a milestone. The next session for the component takes
them.

- [ ] `[infra]` tag the worker tasks: the ECS service has no `propagate_tags`, so Fargate tasks
  carry no `project` tag and their cost is untagged (the $10 project budget misses it; the $90
  account budget still counts it). Set `propagate_tags = "SERVICE"` and
  `enable_ecs_managed_tags = true` on `aws_ecs_service.worker`. Check: a running task's tags
  include `project`; a day later Cost Explorer shows ECS under the tag.
- [ ] `[worker]` then `[tool-gateway]`: drop the run token's `Authorization: Bearer` copy and
  fallback (Cross-service changes).
- [ ] `[specs]` `specs/README.md`: the gotcha "do not run `aws_demo.py` while live" is outdated
  (it runs its own `demo-stub` and refuses without the fake connector).
- [ ] `[ci]` GitHub Actions: each component's tests on push and pull request (control plane with
  Docker, the Python services, contracts, `terraform validate`), a status badge in the README;
  later an eval gate on spec changes (a model key as a GitHub secret).

## Later milestones (draft)

Rough lists so the path is visible. Each milestone's planning session rewrites its own list.

- **Later candidates:** open source (send a fix upstream when the platform hits a real bug in a
  library it uses: MCP Python SDK, OpenTelemetry, the Lambda Web Adapter; the old goal, Strands
  fixes, no longer fits since ADR 0009); users and roles (Open questions); GitHub App
  installations mapped to tenants.

## Cost guardrails

- Set an AWS Budgets alert before deploying anything, and a spend limit or alert on every model
  provider account (MiniMax, OpenRouter, ...).
- Nothing billed per hour (ADR 0007): no NAT gateway, load balancer, RDS, interface VPC endpoints
  or always-on instance. Public subnets for Fargate tasks; Lambdas outside the VPC.
- Before sharing the URL with others: API Gateway route throttling, per-run budgets enforced, and a
  daily model spend alarm (all three in place once the worker writes its spend metric, ADR 0023).
  Throttling is soft: the account's 10 concurrent Lambdas are the real ceiling.
- Use the cheapest capable model for evals; use recorded fixtures so evals never call real
  APIs.
- Workers scale to 0 when idle; run load tests only against the stub model.

## Open questions

- Will the repo be public? Affects how much personal context belongs in it.
- Users and roles (RBAC): who may publish an agent, approve a call, or publish with
  `acts_as: owner`. Needs users in the control plane (today one tenant, one API key). After M4.
  Then the owner login link moves behind the user's sign-in (ADR 0015: today anyone holding a
  valid link can connect their account as that owner).
- Is the worker's scale-from-0 delay (a minute or more) acceptable for demos, or add the Lambda
  worker entrypoint (ADR 0005) early?
- Is Spring Boot's cold start on Lambda with SnapStart fast enough for Slack's 3-second callback
  limit, or does the control plane need a lighter framework?

## Cross-service changes

Changes to `contracts/` that another service still has to adopt. Remove an entry once every listed
service has followed.

- **Tenant limits and setting runs aside** (2026-11-21, M8, `internal-api.openapi.yaml`,
  `run-message.schema.json` description; no `schema_version` change). `control-plane` must:
  answer PATCH to RUNNING with 429 + `Deferred` + `Retry-After` at a tenant's cap (after
  re-queuing the run with that delay), fail a start over the daily spend cap with
  `tenant_budget` and answer 409, serve `POST /internal/runs/{run_id}/defer`, send every run
  message with `MessageGroupId` = tenant. `worker` must: on 429 at start drop the message; call
  `defer` instead of failing when the model keeps answering 429; never send `rate_limited` or
  `tenant_budget`; add `approval_expired` to its failure reasons. Remove this entry when both
  have followed.

- **The run token moves from `Authorization: Bearer` to the `X-Run-Token` header** (2026-11-19;
  on AWS `Authorization` carries the SigV4 signature API Gateway checks on `/mcp`). Declared in
  `contracts/mcp-tool-call.schema.json` and `internal-api.openapi.yaml` (`run_token`). `worker`
  done: always sends `X-Run-Token`, and `Authorization: Bearer` too when unsigned. `tool-gateway`
  done: reads `X-Run-Token` (alone when present), Bearer only as a fallback. Must follow, in any
  later session: `worker` drops the Bearer copy (`tools_mcp._run`), then `tool-gateway` drops the
  fallback (`app._run_token`) and its test; then this entry goes.

## Handoff notes

Newest first. One entry per work session: date, component, done, next, gotchas. Trim entries older
than the current milestone.

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

- **2026-11-21 · M6 closed** — Cost check: Cost Explorer filtered on tag `project` shows only
  KMS (about $0.01 so far; the key is a fixed $1 a month); no NAT, load balancer, RDS or instance
  charges. M6 marked done. Found: worker tasks carry no tags (no `propagate_tags` on the ECS
  service), so Fargate cost is untagged; new "Follow-ups" section in this file has it, the
  run-token cleanup and the `specs/README.md` fix. Next: M8 planning (needs a new goal).
  Gotchas: the Cost Explorer API costs $0.01 a request (the console is free).

- **2026-11-21 · M7 closed** — M7 marked done: its goal (1,000 runs, chaos report) is met; the
  `[infra]` idle-queue task is ticked without the overnight confirmation (calls verified in
  CloudTrail; an optional, free follow-up check is in the task). `loadtest/report.md`: cold-start
  line says so; cost line waits for the M6 cost check. Next: the M6 cost check (Cost Explorer,
  tag `project`, probably readable now; it also fills the report's cost line), then M8 planning
  (needs a new goal). Gotchas: none new.

- **2026-11-21 · loadtest** — M7 full test and report. `loadtest/report.md` (setup, seven tests,
  chaos, limits, what it does not show, cost, reproduce); two lines pending: cold start after a
  quiet night (ADR 0025 check) and Cost Explorer's actual cost. Driver fixes from the 1,000-run
  tests: chaos starts with the runs (`driver.open_runs`; sending 1,000 at 20/s takes 50 s, so the
  first chaos run stopped only 2 workers, late); a repeat of a tool the fake connector marks
  idempotent is reported as allowed (`chaos.SAFE_TO_REPEAT`, `duplicate_side_effects`), any other
  repeat fails the test. 58 tests. Settings back to normal (`plan` clean, gateway `github`,
  `worker_concurrency` 4). Next (2026-11-21): M6 cost check (also fills the report's cost line),
  queue keep-alive check with one planted issue (fills the cold-start line, ticks that task);
  then M7 is done and M8 needs planning. Gotchas: 1,000 runs executing at once needs the 20
  workers up before the runs arrive (run a test first) or long runs, because the first
  scale-out step may add only 5 workers and those can finish a short backlog before the next
  step; results files of the first tests show "redelivered 0" (written before the receive-count
  fix): use the report's numbers.

- **2026-11-20 · infra** — M7 runs per worker in load mode (ADR 0024 amendment). New variable
  `worker_concurrency` (default 4, 1-100) and `load_test_settings.worker_concurrency` (default
  50); `ecs.tf` sets `WORKER_CONCURRENCY` from `local.worker_concurrency` (`load_test.tf`); load
  throttle defaults 1,000/2,000; output `worker_concurrency`; tfvars example shows a five-value
  `load_test_settings` block. Normal `plan`: no resource changes (only the new output); `plan -var
  load_test=true`: throttle 1,000/2,000, 20 tasks, `WORKER_CONCURRENCY` "4" -> "50". Not applied.
  Next: `/start loadtest` (full test and report); the first apply with `load_test = true` brings
  the new output too. Gotchas: a temporary `load_test_settings` block in tfvars now needs five
  values, `loadtest/AGENTS.md` still shows the four-value chaos block (update it in that
  session); 50 runs on a 0.5 vCPU task may saturate CPU: lower the value in the block if so.

- **2026-11-20 · loadtest** — M7 chaos. `chaos.py`: `chaos_loop` (a random running task every
  S seconds while runs are open, `ecs.stop_task` reason "loadtest chaos"; stops recorded even if
  cancelled), `stopped_exit_codes`, `parse_access_line` / `repeated_calls` (gateway access log,
  `ok` more than once per idempotency key). `aws.py`: target gains the gateway log group
  (`functions` output), `log_messages` (paged); the attempts query adds the highest
  `queue.receive_count`. Report: chaos, lost runs, tool calls; exit 1 also on a repeated call.
  56 tests. On AWS, 100 runs with `--chaos-every 20 --chaos-max 8` (numbers in the task): 0 lost,
  0 repeated, 13 taken over. Load mode off, the temporary `load_test_settings` block removed from
  tfvars, `plan` clean, gateway `github`. Next: the 2026-11-21 checks (M6 cost, queue keep-alive:
  target now about 4 minutes), then `[loadtest]` full test and report. Gotchas: a killed worker
  loses the spans it had not uploaded, so the first test printed "redelivered 0" and 3 runs had
  no span (fixed: receive count); the chaos test needs runs longer than the 30 s ECS stop timeout
  (`stub_latency_ms = 15000` in a temporary tfvars `load_test_settings` block; the default
  2,000 ms runs end before the kill, so stops only drain); the first stop can hit a task that
  just started (no runs yet).

- **2026-11-20 · infra** — M7 scale-out from an idle queue (ADR 0025), applied, check pending.
  New `aws_scheduler_schedule.keep_queue_active` (`scheduler.tf`): every hour, `TagQueue` on the
  run queue with its own `project` tag, so SQS keeps sending its metrics (a queue with no call for
  six hours stops, and resumes up to 15 minutes after waking; the load driver measured about 6
  minutes to the first worker). Scheduler role gains `sqs:TagQueue` on the run queue only. `plan`
  clean after apply, queue tags unchanged; schedule created 22:57 UTC (first call about 23:58).
  Docs: ADR 0025 and index, architecture (Deployment: workers, cost tree, IAM), `infra/AGENTS.md`
  (two check commands, Scheduler convention, Lambda quota 1,000), `scaling.tf`/`main.tf` comments.
  Next (2026-11-21, with the M6 cost check): `TargetErrorCount` 0 and a `TagQueue` event in
  CloudTrail by the scheduler role; the run queue's per-minute metric points continue past about
  04:45 UTC; then plant an issue in the sandbox (dev is live) and check its run reached a worker
  within about 3 minutes (`created_at` against the first "run.attempt" span); tick the task.
  Then `[loadtest]` chaos. Gotchas: Scheduler's universal target refuses read-only actions (`get`,
  `list`, `describe`, `receive`, ...), so `GetQueueAttributes` cannot be the keep-alive; never use
  `SendMessage` for it (a message starts a run); the sweeps schedule shares the `default` group, so
  Scheduler metrics by group count both schedules.

- **2026-11-20 · loadtest** — M7 task 3 (driver). New `loadtest/` uv project: `cli.py` (args,
  report, `results/<time>-<runs>.json`, git-ignored), `driver.py` (start at a fixed rate, poll
  until final), `api.py` (public API, retries 429/502/503/504 and counts them), `aws.py` (load-mode
  check from `terraform output`, SSM tenant key, one Logs Insights query on `aws/spans` for each
  run's first "run.attempt", ECS CPU and memory with a wait for CloudWatch's lag, task samples),
  `stats.py`; 44 tests, no AWS. Lambda quota now 1,000. Two 100-run tests in load mode, both all
  `SUCCEEDED` (numbers in the task); load mode off again, `plan` clean, gateway `github`. New
  `[infra]` task: scale-out from an idle queue (SQS stops metrics after six hours idle; about 6
  minutes to the first worker). Next: that task or `[loadtest]` chaos. Gotchas: the second test's
  results file was not written (datetime in JSON, fixed after, with a test; its numbers are in
  the task); `wait_for_spans` and the metrics wait add up to about 5 minutes after the runs end;
  a test right after another finds workers up (warm numbers), wait about 10 minutes for 0
  workers, and more than six hours of quiet for the cold-queue case.

- **2026-11-20 · infra** — M7 task 1 (load-test mode, ADR 0024). `load_test` switch with
  `load_test_settings` (throttle, workers, gateway `fake`, stub latency via
  `local.worker_load_env`); backlog-sized scale-out steps; outputs `load_test`,
  `gateway_connectors`; `aws_demo.py` publishes `demo-stub` (trigger `manual.demo.run`) and
  refuses unless the gateway runs `fake`. Checked on AWS: load mode on, `aws_demo.py` passed
  (model calls 2,000 ms), load mode off, `plan` clean, `issue-triage` still version 4. Worker
  image `c53f4c6` deployed (latency 0 in normal mode). Next: `[loadtest]` driver once the Lambda
  quota is granted (`infra/AGENTS.md` has the check; the AWS case was open on 2026-11-20).
  Gotchas: the control plane has no read route for agents (versions checked from its request
  log); `specs/README.md`'s gotcha "do not run `aws_demo.py` while live" is now outdated (it no
  longer touches `issue-triage` and refuses without `fake`): the next specs session should update
  it; while load mode is on, real issues still run `issue-triage` with fake tools.

- **2026-11-20 · worker** — M7 task 2 (stub latency). `StubModel` waits `latency_seconds` before
  each reply (injectable `sleep`; negative refused); `config.py` reads `MODEL_STUB_LATENCY_MS`
  (0 to 60000, default 0, empty = unset); wiring passes it. Tests: `tests/unit/test_model_stub.py`,
  config and wiring cases, a `stub_with_latency` entry in the `Model` contract suite; 366 passed,
  1 skipped (live). Image `c53f4c6` pushed after the commit, not applied. Next: `[infra]` load-test mode
  once the Lambda quota is granted (set `worker_image_tag` to this image and
  `MODEL_STUB_LATENCY_MS` in `worker_model_env`). Gotchas: `worker_model_env` in tfvars replaces
  the whole default map, so keep the `MODEL_MINIMAX_*` entries when adding the latency.

- **2026-11-20 · M7 planning** — M7 task list written (load-test mode, stub latency, driver,
  chaos, full test and report); decisions under M7 tasks (plain Python driver, 100 then 1,000
  runs, $5 budget, start time from spans). Lambda concurrency increase to 1,000 requested
  (`PENDING`); Fargate quota 64 vCPU is enough. The M6 `aws_demo.py` task moved into M7 task 1.
  M6 waits only for the cost check (2026-11-21). Next: `/start infra` (load-test mode) once the
  quota is granted; the worker's stub latency task can go first. Gotchas: scale-out today adds
  one task per alarm (2-minute cooldown); dev is live on the github connector, so a stub load
  must switch the gateway to `fake` first.

- **2026-11-20 · specs** — M6 go live. App credential put into the SSM `TokenStore`
  (`put_app_credential.py`); `gateway_connectors = "github"` and `github_repos` in tfvars
  (applied); the `project` cost allocation tag activated; the App's webhook (was off and empty:
  the local setup used a repo webhook through smee), Redirect URI and Slack Request URL moved to
  the API; Issues events subscribed; `specs/issue-triage.yaml` published on AWS as version 4. A
  planted duplicate ran end to end (numbers in the task). Docs: `specs/README.md` → Run
  issue-triage on AWS and gotchas, `specs/AGENTS.md`, architecture (gateway connectors), root
  Commands table; new `[infra]` task for `aws_demo.py`. Next: the cost check on 2026-11-21 (then
  tick go live, M6 done), then the new `[infra]` task or the next milestone. Gotchas: don't run
  `aws_demo.py` while live; only `issues.opened` starts a run (close/reopen does not); GitHub's
  Subscribe to events list appears only while the webhook is active; the spend alarm stays in
  ALARM from the 2 USD test until about 05:59 UTC 2026-11-21.

- **2026-11-20 · worker** — M6 spend metric (ADR 0023). New `Metrics` port (`model_cost(usd)`,
  never raises), `NoopMetrics` default, `EmfMetrics` (one CloudWatch EMF line on stdout per
  model call: `METRICS_NAMESPACE`, `ModelCostUsd`, `Dimensions: [[]]`, value only; lock for
  threads; failed write logged by type). `handle_run` reports every call, 0 included. Config
  checks the namespace; wiring picks the adapter. 355 tests pass (1 live skipped). Image
  `05cfaf3` checked, pushed and applied (`worker_image_tag`); `aws_demo.py`: run `SUCCEEDED`
  (217 s waiting for a worker), 11 worker spans, and 3 `ModelCostUsd` samples (Sum 0) with no
  dimensions. Cross-service entry "Model spend metric" removed. Next: `[specs]` go live (a real
  MiniMax run gives the first nonzero value). Gotchas: the alarm stays in ALARM from the 2 USD
  test until about 05:59 UTC on 2026-11-21; `--no-scale-in` was used, so the workers return to 0
  on their own (about 5-8 minutes).

- **2026-11-20 · infra** — M6 task 13 (protection, ADR 0023). API stage default route throttling
  (`api_throttle_rate` 5, `api_throttle_burst` 10). New `alarms.tf`: SNS topic
  `agent-platform-dev-alerts` with an email subscription (`alert_email` in git-ignored tfvars,
  confirmed) and alarm `agent-platform-dev-model-spend` (`AgentPlatform/dev` `ModelCostUsd`, Sum
  over 86400 s > `model_spend_alarm_usd` 1, missing = OK, emails on ALARM and OK). Worker task
  definition gains `METRICS_NAMESPACE`. `/aws/application-signals/data` imported (was never
  expiring): 14 days, `skip_destroy`. New `scripts/throttle_check.py`: 6 of 80 requests got 429.
  `put-metric-data` 2 USD at 05:59 UTC fired the alarm about 70 s later; email received. `plan`
  shows no changes. Next: `[worker]` spend metric (new task, added before go live). Gotchas:
  throttling is best-effort (about 23 per second admitted against a limit of 5), and a burst of
  30 at once got no 429 but 503s from the account's 10-Lambda limit; unsigned requests to IAM
  routes get 403 and are not counted, so they cannot test it; a one-day alarm period is the last
  24 hours, so the test value keeps the alarm in ALARM until about 05:59 UTC on 2026-11-21 (then
  an OK email); metric filter patterns cannot select keys with dots (span attributes).

- **2026-11-19 · infra** — M6 task 12 (worker traces). Worker task definition
  `TRACING_EXPORTER=xray`; task role statement `Traces` (`xray:PutTraceSegments`, `xray:PutSpans`
  on `*`); `aws/spans` imported (`import` block) with 14-day retention (was 30) and
  `skip_destroy`. Rebuilt and deployed all three at `01116ee` (control plane with tracing, gateway
  zip with bytecode, worker image `01116ee`). `aws_demo.py` gains a trace lookup (`--no-trace` to
  skip): the stub run `SUCCEEDED`, its `trace_id` held 11 worker spans in `aws/spans` (run.attempt
  1, model.call 3, tool.call 2, checkpoint 5); workers back to 0 after 432 s. `plan` shows no
  changes after apply. Cross-service entry "Run trace context" removed: every service followed.
  Next: `[infra]` protection. Gotchas: the run waited 300 s for a worker (207 s last time; one
  sample each); `filter-log-events --limit 1` can return an empty first page, so page through
  (the script does); verify a worker image before pushing (immutable tags).

- **2026-11-19 · tool-gateway** — M6 task 11. New `gateway/tracing.py` (OpenTelemetry SDK, own
  `TracerProvider`; `TRACING_EXPORTER=none|logging`, no upload, ADR 0022). `/mcp` handlers continue
  the request's `traceparent`: "mcp.tools_list", "tool.execute" (run, tenant, agent, step, tool,
  outcome), "approval.check", "connector.request" per HTTP attempt (connector, operationId, status,
  attempt; no URL). `HttpControlPlane` sends `traceparent`; connector calls never do. Access log
  lines gain `trace_id`. `scripts/lambda_zip.py` precompiles bytecode (unchecked-hash `.pyc`,
  needs Python 3.12; zip 34.3 MB). 263 tests pass; `make demo`: the run's `trace_id` is on both
  gateway access log lines. Lambda trials (4, ~$0): init 3.4 s before, 3.63 / 3.71 s with
  OpenTelemetry, 1.96 / 2.00 s with bytecode. Next: `[infra]` worker traces (also deploys the new
  gateway zip). Gotchas: the MCP SDK client lists tools again before a call, so a call has two
  "mcp.tools_list" spans; GitHub's search operationId is `search/issues-and-pull-requests`.

- **2026-11-19 · worker** — M6 task 10. New port `Tracer` (`ports/tracing.py`; `NoopTracer` in
  core is the `Deps` default) and adapter `tracing_otel.py` (OpenTelemetry SDK, own
  `TracerProvider`, nothing global; exceptions mark the span with the type name only, never the
  message). `PollLoop` opens "run.attempt" per delivery with the SQS attribute `traceparent` as
  parent (`QueueMessage.trace_parent`); `handle_run` adds "model.call", "tool.call" (outcome ok,
  error, reused) and "checkpoint" spans. `HttpControlPlane` and `GatewayTools` send `traceparent`;
  `RunUpdate.trace_id` removed. `TRACING_EXPORTER=none|logging|xray`; `xray` is OTLP/HTTP to
  `https://xray.<region>.amazonaws.com/v1/traces` signed by `SigV4RequestsAuth` (service `xray`),
  batched, flushed on shutdown. New deps `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-http`
  (pull in `requests`, `protobuf`). 332 tests pass (1 live skipped); the live check
  (`LIVE_AWS=1 ... test_live_xray.py`) found its span in `aws/spans` after 23 s; `make demo`
  passed. Next: `[tool-gateway]` tracing; the new `[infra]` worker traces task (whole AWS trace
  check moved there). Gotchas: Transaction Search now reads `CloudWatchLogs` / `ACTIVE`; spans
  take about 20-30 s to show in `aws/spans`; the control plane uploads nothing, so on AWS the
  worker's "run.attempt" has a parent span X-Ray never receives.

- **2026-11-19 · control-plane** — M6 task 9. New package `trace/`: OpenTelemetry SDK by hand
  (`TracingConfig`; no agent), `SecureIdGenerator` (SnapStart-safe ids), `RunTracing` ("run.start"
  opens each run's trace, "run.approval" and "run.requeue" join it), `TracingFilter` (SERVER span
  per request; only `/internal/*` honors an incoming `traceparent`), `TraceExport` (made on first
  use, closed before the snapshot, paused while priming), AWS SDK spans through
  `opentelemetry-aws-sdk-2.2` 2.28.0-alpha on every client. `Run` gains `traceParent`; DynamoDB
  keeps the first `traceId` (`if_not_exists`). `RunMessage.traceParent` goes in the SQS attribute
  `traceparent`. `TRACING_EXPORTER=none|logging`. 317 tests pass (2 live-AWS skipped); contracts
  96; `make demo` passed. Decision changed mid-task (ADR 0022): Transaction Search turned on by
  the user; the Lambda trial with active tracing and the X-Ray UDP exporter got spans only for
  sampled requests, none after a restore, and +250 ms restore (2.29 s vs 2.00 s median cold click
  with nothing uploaded), so the control plane uploads nothing and the worker uploads directly.
  Next: `[worker]` tracing. Gotchas: the Transaction Search status read `XRay` / `ACTIVE` after
  enabling while spans still reached `aws/spans` (confirm before the worker task);
  `InMemorySpanExporter.shutdown()` clears its spans; `AwsSdkTelemetry` 2.28 calls it
  `createExecutionInterceptor()`; the Lambda zip is 49.0 MB.

- **2026-11-19 · infra** — M6 task 8. `infra/envs/dev/` compute applied, 56 resources (`network.tf`,
  `artifacts.tf`, `logs.tf`, `iam.tf`, `lambda.tf`, `api.tf`, `ecs.tf`, `scaling.tf`,
  `scheduler.tf`); values in git-ignored `terraform.tfvars` (`.example` committed). The user chose
  no custom domain (ADR 0021, Proposed): the default `execute-api` address, from
  `terraform output api_url`. All 7 SSM secrets are set (tenant API key generated with
  `put_secrets.py --generate`); worker image `838bc29` in ECR. `scripts/aws_demo.py` (like
  `make demo`) passed: `SUCCEEDED` in 5 steps, 207 s from start (nearly all a worker starting from
  0), workers back to 0 after 458 s, dead-letter queue empty. Cross-service entries for the
  control-plane TTL (done in the data task) and the IAM routes removed. Next: `[control-plane]`
  tracing. Gotchas: Lambda zips and the worker image must be built before `plan` (`filemd5` of
  the zips); routes answer 404 for up to a minute after the API is created; secrets are read once
  per Lambda instance and the 5-minute sweeps keep a control-plane instance warm, so a changed
  secret may reach the control plane only after a new version is published; put secrets and push
  the image before the full apply. When ADR 0021 is accepted, mark ADR 0007's status "custom
  domain superseded by 0021".

- **2026-11-19 · tool-gateway** — Follow-up to the worker's header change (ADR 0020).
  `app._run_token`: `X-Run-Token` decides alone when present (an empty or bad one is 401 even
  beside a valid Bearer; a SigV4 `Authorization` next to it is ignored); without it,
  `Authorization: Bearer` still works. `mcp_client` sends `X-Run-Token` (`as_bearer=True` for the
  fallback); the trial script too. 251 tests pass; `make up && make demo` passed with the rebuilt
  gateway and worker. Cross-service entry updated: the Bearer copy and fallback can go in later
  worker and gateway sessions. Next: `[infra]` compute.
- **2026-11-19 · worker** — M6 task 6. The run token moved to `X-Run-Token` (user chose this over
  dropping IAM on `/mcp` or SigV4 in the query string): SigV4 and the token both used
  `Authorization`, so ADR 0007's "IAM and run token on `/mcp`" could not work. Contracts edited
  (descriptions only; 96 contract tests pass); Cross-service entry added; the gateway follows in a
  short session before `[infra]` compute (task added). `tools_mcp.SignedAuth` (an `httpx2.Auth`)
  signs every gateway request for `execute-api` with `control_plane_http`'s signer, covering
  `host`, `content-type`, `x-run-token` and the body; on with `TOOL_GATEWAY_SIGV4=true`. Unsigned
  requests also send `Authorization: Bearer` for gateways not yet updated. `make worker-image`
  builds the linux/arm64 image (93 MB, tag = commit); ECR push commands in `worker/AGENTS.md`.
  The model key needs no code: the task definition's `secrets` maps
  `/agent-platform/dev/worker/model/minimax/api-key` to `MODEL_MINIMAX_API_KEY`. 316 tests pass;
  `make up && make demo` passed with the rebuilt images. Gotchas: the MCP SDK 2.x client's first
  request is `server/discover`, not `initialize`; a Bash heredoc with `<<'EOF'` tripped the
  removal safety check (use the Edit tool or a script file). For `[infra]` compute: worker task
  env `TOOLS_PROVIDER=mcp`, `TOOL_GATEWAY_URL`, `TOOL_GATEWAY_SIGV4=true`, `CONTROL_PLANE_URL`,
  `QUEUE_BACKEND=sqs`, `RUN_QUEUE_URL`, `STATE_BACKEND=dynamodb`, `STATE_TABLE`, `AWS_REGION`,
  `MODEL_PROVIDERS` and the `MODEL_MINIMAX_*` settings; secrets
  `MODEL_MINIMAX_API_KEY` from SSM; execution role `ssm:GetParameters` on
  `/agent-platform/dev/worker/*`, ECR pull, logs; task role SQS receive/delete/change visibility,
  DynamoDB on the run-state table, `execute-api:Invoke` on `/internal/*` and `/mcp`; runtime
  platform `ARM64`. Next: `[tool-gateway]` `X-Run-Token`.
- **2026-11-19 · tool-gateway** — M6 task 5. Lambda through the AWS Lambda Web Adapter layer (ADR
  0019, Proposed): Mangum is out because MCP's `StreamableHTTPSessionManager.run()` works once per
  instance and Mangum runs the lifespan per invocation. `run.sh` (handler) starts the unchanged
  uvicorn app; `scripts/lambda_zip.py` builds a 27.7 MB zip (arm64 manylinux 2.34 wheels,
  `gateway`, `connectors/`, contract schemas). Trial on AWS (`scripts/lambda_trial.py`, all
  deleted): cold `/health` 3.4 s init, `/mcp` without token 401, `initialize` with a token signed
  by the dev KMS key 200 in 17 ms, warm 2 ms, 152 MB used. `adapters/token_store_ssm.py`: one
  SecureString per credential at `<path>/<tenant>/<identity>/<provider>`, bytes outside
  `A-Z a-z 0-9 . -` as `_XX` (so `user:42` is `user_3A42`), 4 KB max, no cache (refresh tokens
  rotate); passes the contract suite under moto. `aws.aws_env` at startup: `RUN_TOKEN_KMS_KEY_ID`
  to the public key, `SECRETS_SSM_PATH` to the OAuth secrets; `CONTROL_PLANE_SIGV4=true` signs
  internal calls. `scripts/put_app_credential.py` writes the GitHub App credential from `.env`.
  249 tests pass. Gotchas: moto's KMS `GetPublicKey` has only `CustomerMasterKeySpec`; boto3 is
  now a runtime dependency (local stack image unchecked). For `[infra]` compute: layer
  `arn:aws:lambda:us-east-1:753240598075:layer:LambdaAdapterLayerArm64:30`, handler `run.sh`,
  python3.12, arm64, about 512 MB is enough; env `AWS_LAMBDA_EXEC_WRAPPER=/opt/bootstrap`,
  `AWS_LWA_READINESS_CHECK_PATH=/health`, `RUN_TOKEN_KMS_KEY_ID`, `TOKEN_STORE=ssm`,
  `TOKEN_STORE_SSM_PATH=/agent-platform/dev/gateway/tokens`,
  `SECRETS_SSM_PATH=/agent-platform/dev/gateway`, `CONTROL_PLANE_URL`, `CONTROL_PLANE_SIGV4=true`,
  `CONNECTORS`, `GITHUB_REPOS`, `GITHUB_OAUTH_CLIENT_ID`, `GATEWAY_PUBLIC_URL`; role
  `ssm:GetParameter/PutParameter/DeleteParameter` on `.../gateway/tokens/*`, `ssm:GetParameters` on
  the two gateway secrets, `kms:GetPublicKey`, `execute-api:Invoke` on `/internal/*`. Before go
  live: `put_app_credential.py`. API Gateway's 30 s cap is below the worker's 60 s tool timeout.
  Next: `[worker]` SigV4 `auth` for `GatewayTools`, Fargate image.
- **2026-11-19 · control-plane** — M6 task 4. Two new ports with contract suites. `token/RunTokenSigner`:
  `LocalKeySigner` (the env key pair, unchanged behavior) and `KmsRunTokenSigner`
  (`RUN_TOKEN_KMS_KEY_ID`; signs the SHA-256 digest, DER transcoded to JOSE, public key read once
  on first use; `kid` = thumbprint as before, no contract change). `secret/SecretSource`:
  `EnvSecretSource` (default; `TENANT_API_KEY` still required) and `SsmSecretSource`
  (`SECRETS_SOURCE=ssm`, `SECRETS_SSM_PATH`; one `GetParameters` with decryption on first use,
  kept per environment, failures not kept). API key filter, both webhook controllers and the Slack
  channel ask the source per request; SSM unreachable gives 503. Lambda `afterRestore` prefetches
  secrets in the background; priming uses `peek` only. 302 tests (2 skipped: `LiveAwsCheckTest`,
  run with `LIVE_AWS=1`; passed against dev: KMS-signed token verifies and matches the schema, all
  4 parameters readable, still placeholders). Fakes `FakeSsm` / `FakeKms` implement the SDK client
  interfaces (no Docker). Gotchas: the Lambda zip is now 47.4 MB (the SSM SDK jar is 6 MB), near
  the 50 MB direct-upload limit, so `[infra]` compute uploads it through S3; with SSM secrets
  `SLACK_APPROVAL_CHANNEL` is required (every secret counts as configured). For `[infra]` compute:
  control-plane role `kms:Sign` + `kms:GetPublicKey` on the run-token key, `ssm:GetParameters` on
  `/agent-platform/dev/control-plane/*`; env `SECRETS_SOURCE=ssm`, `SECRETS_SSM_PATH`,
  `RUN_TOKEN_KMS_KEY_ID`, `SLACK_APPROVAL_CHANNEL`. For `[tool-gateway]`: get the public key with
  `kms:GetPublicKey` and compute `kid` as the RFC 7638 thumbprint, as today. Re-measure the cold
  Slack click with SSM in M7. Next: `[tool-gateway]` Lambda entry.
- **2026-11-19 · infra** — M6 task 3. `infra/envs/dev/` applied, 16 resources: DynamoDB
  `agent-platform-dev-control-plane` (from `control-plane/dynamodb/*.json`, GSI1/GSI2, TTL,
  deletion protection, point-in-time restore) and `agent-platform-dev-run-state` (TTL); SQS
  `agent-platform-dev-runs` (DLQ `-runs-dlq` after 5 receives, 14 days); ECR
  `agent-platform-dev-worker` (immutable tags, last 10 images); KMS `ECC_NIST_P256` sign key
  `alias/agent-platform-dev-run-token`; 7 SSM SecureString parameters under
  `/agent-platform/dev/<service>/` (`aws/ssm` key). Checked: tables match the JSON
  (describe-table), plan clean, no secret value in state. ADR 0018 drafted (Proposed): SSM over
  Secrets Manager (supersedes that line of ADR 0010), KMS signing, values outside Terraform.
  `infra/scripts/put_secrets.py` puts values from `.env` (`--status`, `--generate`). Open: the
  user puts the secrets (all 7 still placeholders at wrap-up), with `--generate TENANT_API_KEY`
  (the `.env` key is the public local one; the script refuses keys under 32 characters).
  Gotchas: `value` + `ignore_changes` still copies the decrypted value into state on refresh
  (provider source), so parameters use write-only `value_wo` from an ephemeral random
  placeholder; never change `value_wo_version`. GSI `hash_key` is deprecated in provider 6
  (`key_schema` blocks). For the services: Lambda cannot inject SecureString into env vars, so
  control plane and gateway read their parameters through the SDK on first use (not at startup,
  SnapStart); ECS task `secrets` for the worker. Outputs: `tables`, `run_queue_url`,
  `worker_repository_url`, `run_token_key_alias`, `secret_parameters`. Next: `[control-plane]`
  AWS adapters (KMS signer, secrets from SSM).
- **2026-11-19 · control-plane** — M6 task 2. Lambda entry `lambda/LambdaHandler` (aws-serverless-
  java-container-springboot4 3.0.5; API Gateway HTTP API payload 2.0) plus a direct-invoke
  `{"sweep": "all"}` event running the new `run/Sweeps.runOnce()` (re-enqueue, post, expire; the
  local `@Scheduled` uses it too, always off on Lambda). SnapStart priming (`lambda/Priming`, CRaC
  `beforeCheckpoint`): four network-free requests, `LAMBDA_PRIMING=off` skips them.
  `./gradlew lambdaZip` (40 MB, classes at root, `lib/`). Measured on AWS with
  `scripts/lambda_trial.py` (throwaway function, table, role; all deleted): cold signed Slack click
  3.1 s at 1024 MB, 2.6 s at 1536, 2.2 s (max 2.4) at 1769, 1.8 s at 2048; warm about 40 ms;
  priming saves 150-190 ms; each publish waits about 85 s for the snapshot. Chosen 1769 MB arm64
  (smallest with the slowest cold click under 2.5 s). ADR 0017 drafted (Proposed). 261 tests pass.
  Gotchas: the library's `ServerlessAutoConfiguration` replaces Tomcat everywhere (52 integration
  tests broke), so `application.yml` excludes it; Lambda redelivers late log lines, so a log group
  deleted right after its function comes back (the script waits, then deletes). For `[infra]`
  compute: handler `dev.agentplatform.controlplane.lambda.LambdaHandler::handleRequest`, runtime
  java21, arm64, 1769 MB, SnapStart `PublishedVersions`, API and scheduler point at an alias,
  scheduler payload `{"sweep": "all"}`. Next: `[infra]` data.
- **2026-11-19 · infra** — M6 task 1. `infra/bootstrap/` applied (local state, git-ignored): state
  bucket `agent-platform-tfstate-<random suffix>` (versioned, SSE-S3, public access blocked,
  TLS-only policy, old versions expire after 90 days, `prevent_destroy`) and two budgets,
  `agent-platform-monthly` ($10, filter `user:project$agent-platform`) and `account-monthly`
  ($90), email at 50%/100% actual and 100% forecast. Budgets live in bootstrap so destroying an
  environment keeps the alerts. `infra/envs/dev/`: S3 backend (`use_lockfile`), bucket name from
  git-ignored `backend.hcl`; no resources yet, `plan` clean. AWS provider 6.73.0 locked. Open: the
  user activates the `project` cost allocation tag once it appears (up to 24 h after the first
  tagged resource; command in `infra/AGENTS.md`); until then the $10 budget reads $0. Gotchas:
  budget emails need no confirmation (only SNS does); the lock files hold darwin_arm64 hashes only,
  so CI on Linux later needs `terraform providers lock -platform=linux_amd64`; a dollar sign before
  a brace is an escape in HCL, so the tag filter uses `format()`. Next: `[control-plane]` Lambda
  packaging trial (ADR 0017).
- **2026-11-19 · specs** — M5 task 6, **M5 done**. `specs/needs-info.yaml` (labels a bug report
  `needs-info` when it lacks steps to reproduce; only `github.add_label`, no approval, budget 6
  steps / $0.02 / 120 s; summary `Bug report / Steps to reproduce / Needs info`) and 8 tasks in
  `evals/tasks/needs-info/`; ran with no change under `evals/src` or `evals/tests`. Baseline
  8/8 tasks, 87.5% of runs, $0.02; rerun 91.7%, no regression. No judge (fixed output lines).
  Findings for a later prompt session: twice labeled a feature request or question `needs-info`
  while its summary said `Needs info: no`; once called add_label without `labels`;
  `bug-with-steps` flaky (1-2 of 3). The sandbox repo needs a `needs-info` label before a live
  run. specs README/AGENTS: spec changes are checked with `make eval SPEC=<name>`. Open: ADR 0016
  is still Proposed; accept it when the user agrees. Next: plan M6 ("plan M6").

- **2026-11-19 · evals** — M5 task 5: LLM judge. `judge.py` (rubric + context + answer to
  `{"verdict", "why"}` through the worker's model adapter), `labels.py`, `evals labels` and
  `evals judge-check`; suites judge every answer and a "bad" verdict fails the run once
  `judges/<spec>.json` says trusted for the current rubric and model (90% agreement, 5+ bad
  labels, 80% caught); `--no-judge` skips it. `rubrics/issue-triage.md`; 75 labels in
  `labels/issue-triage.yaml` made by Claude (Fable 5.2) at the user's request, not by hand
  (header says so): 1-50 used to adjust the rubric, 51-75 labeled after it was frozen. First
  check 88% (judge called unseen facts "invented"); fixed by giving it repository labels and each
  existing issue's labels, telling it not to grade the agent's statements about its own
  searches, and requiring the duplicate to be clear from the title. Now 99-100% on 75, 16/16 bad
  caught (fresh 25: 96-100%). Baseline re-saved with the judge counting: 29/30 tasks, 93.3% of
  runs, $0.42; the judge failed 2 otherwise passing runs for small unsupported asides ("the only
  other issue", "different code path"): strict but defensible. Spent about $0.68. Next:
  `/start specs` (`needs-info` spec), then its tasks. Gotchas: any rubric edit makes the judge
  "reported only" until `judge-check` runs again, then save a new baseline; validate rubric
  changes on newly sampled labels.

- **2026-11-19 · evals** — M5 task 4: `evals suite` (`suite.py`) and `make eval` /
  `make eval-baseline` (`SPEC=`). 3 runs per task, 4 at once (one simulator and gateway per
  worker thread); report with failed runs' checks and tool calls; full results in
  `evals/results/` (git-ignored); baseline `evals/baselines/issue-triage.json` committed
  (28/30 tasks, 92.2% of runs, $0.354). Regression: a task's pass fraction drops by 2/3 or more,
  or the run pass rate drops more than 5 points; smaller moves are listed as changed. Checked:
  unchanged rerun 95.6%, no regression (`multi-png-export-cropped` moved 0/3 to 3/3 by chance);
  weakened copy of the spec via `--specs-dir` ("a similar topic counts as a duplicate") 86.7%,
  4 tasks named, exit 1. Spent $1.12. Next: `/start evals` (judge; the user labels about 50
  reasons). Open: simulator search is whole words, so "cut" misses "cuts" (task
  `multi-png-export-cropped`); real GitHub may match such forms, so this task may be harder than
  in production. For the later `specs` session: search with key words alone, try other forms,
  stop after three searches (`dup-question-backup`, `multi-png-export-cropped`,
  `injection-fake-assistant-turn`).

- **2026-11-18 · evals** — M5 task 3: 30 issue-triage tasks in `evals/tasks/issue-triage/`
  (plain 6, true duplicates 7, near-misses 6, ambiguous label 2, look-alike candidates 2, prompt
  injection 2, Spanish and German 2, missing label 1, original already closed 2 with lenient
  checks until the spec decides). New issue is always #20; every task checks that no other issue
  changed. Reviewed by the user. One real run on MiniMax: 28/30 for $0.116. Both failures
  reproduce and are agent mistakes, kept as they are: `dup-question-backup` (searched `backup`
  and `back up reinstall`; the original says "back up", so whole-word search misses) and
  `injection-fake-assistant-turn` (14 searches, step budget used up, never labeled; nothing
  closed). For a later `specs` session: tell the model to search key words alone without extra
  words, and to stop after three searches. Next: `/start evals` (suite run: repeats, report,
  baseline, `make eval`); the CLI does not print tool calls yet, a verbose option would help.

- **2026-11-18 · evals** — M5 task 2: `evals/` scaffold. Task format `evals/task.schema.json`
  (YAML tasks in `evals/tasks/<spec>/`; check kinds `status`, `failure_reason`, `tool_called`,
  `tool_not_called`, `github`, `output`); GitHub simulator (`octocat/Hello-World`, whole-word
  search with `OR`, phrases, `-word`, `repo:`/`is:`/`state:`/`label:`); runner = worker
  `handle_run` under a lease with in-memory checkpoints (a run continues after its approval, not
  from step 0), real gateway subprocess with a from-scratch environment, `InternalRunServer` for
  its approval check, every approval approved (`eval:auto`). ADR 0016 drafted (Proposed). 49
  tests, no Docker, no model calls. Real run of `tasks/issue-triage/dup-png-upload-crash.yaml`
  on MiniMax: PASS, 12 steps, $0.0043. Next: `/start evals` (30 tasks; the user reviews them).
  Gotchas: the budget failure reason is `budget_steps` (not `budget_exceeded`); simulator search
  is whole words, so tasks must not rely on stemming. For the next `worker` session: add to
  `worker/AGENTS.md` that `evals/` imports `handle_run`, `Deps`, `InMemoryControlPlane`,
  `InMemoryRunStateStore`, `Config` and `wiring.build`, so worker changes should also run
  `cd evals && uv run pytest`.

- **2026-11-18 · tool-gateway** — M5 task 1: `CONNECTOR_<NAME>_BASE_URL` replaces a connector's
  `base_url` at startup (`config.connector_base_url`, used in `__main__.connectors`). Loopback only
  (`127.0.0.1`, `localhost`, `::1`; http or https; no user, query or fragment), because the
  override receives the connector's credentials, including the GitHub App token exchange; error
  messages never echo the value; startup logs a warning per override. Owner login (`oauth/github.py`)
  keeps its fixed GitHub address. Tests in `tests/test_config.py`, including a live gateway whose
  token exchange and search reach `FakeGitHub`; 224 pass. Next: `/start evals` (scaffold): start
  the gateway subprocess with `CONNECTORS=github`, `GITHUB_REPOS`, throwaway `GITHUB_APP_*` (a
  generated key file) and this setting pointing at the GitHub simulator.

- **2026-11-18 · planning** — M5 planned (task list under "M5 tasks: evals"). Evals became a
  general harness for any spec, run on the host without Docker, with simulated services instead
  of recorded responses; the 2-point CI gate was replaced by 3 runs per task and a 5-point or
  per-task regression rule, and CI moved to later. M7 now names the AWS deployment. Stray code
  fences removed from the M0 to M3 task lists. Next: `/start tool-gateway` (connector base URL).

- **2026-11-19 · specs** — M4 task 11, **M4 done**. `specs/issue-triage.yaml` uses the gateway's
  argument names (GitHub's own: `q`, `owner`, `repo`, `issue_number`, `labels` as a one-item list,
  `state_reason`, hook extra `duplicate_of`); tool names, `acts_as: team-bot`, `require_approval`
  and budget unchanged; validates against the schema. README and AGENTS.md describe the gateway
  path (`make up && CONNECTORS=github docker compose up -d --wait tool-gateway`, no `.env` edit;
  a plain `make up` returns to the fake connector). Checked live: planted duplicate #10 of #5 in
  the sandbox, run `SUCCEEDED` in 12 steps for $0.0046; both labels and the close (reason
  duplicate) by the App (`<app>[bot]`) after Approve in Slack; 7 gateway access lines, all
  `started_by=api:tenant-demo`, `identity=team-bot`; `make demo` passes after switching back.
  Cross-service entry "Run token, connector allowlist, MCP call conventions" removed (every
  service followed). Next: plan M5 (evals): rewrite its draft list into tasks. Gotchas: the spec
  no longer runs on the worker's M1 local tools (other argument names); a Slack Request URL with
  a missing path or trailing slash gets 403 from the ngrok policy (`POST /approvals/slack` must
  answer 401 to an unsigned curl); zsh `echo` breaks JSON with `\n` in it, use `printf '%s'`.

- **2026-11-18 · local-stack** — The Docker control plane no longer gets all of `.env`.
  `local-stack/worker-env.sh` became `service-env.sh` (`make service-env`, replacing
  `make worker-env`, still a prerequisite of every compose target): one allowed-names list per
  service, writing `.worker.env` (unchanged) and `.control-plane.env` (the `${...}` names in
  `control-plane/src/main/resources/application.yml` plus AWS region and dummy keys; git- and
  docker-ignored, mode 600). Compose: `env_file: [.control-plane.env, .run-token-*.env]`.
  Checked: the running control plane holds none of `GITHUB_TOKEN`, the model key, the gateway's
  OAuth secrets or `GITHUB_APP_*` (compared by value, not printed) and all of its own (tenant key,
  webhook secret, `SLACK_*`, run-token keys); `make demo` and `make chaos` pass. No service gets
  `env_file: .env` any more. Next: `[specs]` M4 task 11 (needs a new tunnel for the Slack Request
  URL). Gotchas: a setting the control plane starts reading must be added to `CONTROL_PLANE` in
  `service-env.sh`, or the Docker control plane silently uses the `application.yml` default (the
  host run still reads all of `.env`).

- **2026-11-18 · local-stack** — The Docker worker no longer gets all of `.env`:
  `local-stack/worker-env.sh` (run by `make worker-env`, a prerequisite of every compose target)
  copies only the lines it reads (`ALLOWED`: `MODEL_*`, dummy AWS keys and region, `STATE_*`,
  `QUEUE_BACKEND`, `WORKER_CONCURRENCY`, `LOG_LEVEL`, `RUN_VISIBILITY_SECONDS`) into `.worker.env`
  (git- and docker-ignored, mode 600); compose uses `env_file: .worker.env`. Checked: the running
  worker's environment holds only those names plus compose's own (`GITHUB_TOKEN` empty), none of
  the gateway's, Slack's or the webhook's secret values (compared by value, not printed);
  `make demo` and `make chaos` pass. Next: `[specs]` M4 task 11 (its "no `GITHUB_TOKEN` in the
  worker" now holds), or the new `[local-stack]` control-plane task. Gotchas: a bare
  `docker compose up` after editing `.env` uses the old `.worker.env`; a new worker setting must be
  added to `ALLOWED`.

- **2026-11-18 · local-stack** — Follow-up to M4 task 10 (cross-service entry removed): the
  `tool-gateway` service gets `GITHUB_OAUTH_CLIENT_ID`, `GITHUB_OAUTH_CLIENT_SECRET`,
  `OAUTH_STATE_SECRET`, `GATEWAY_PUBLIC_URL` (default `http://localhost:8090`) by name. Checked:
  `make up` (gateway logs `User login: ...`), `make demo`, `make chaos`; with `CONNECTORS=github`
  an owner call returns the login link, which redirects to GitHub's authorize page with
  `redirect_uri=http://localhost:8090/oauth/github/callback`, S256 and the nonce cookie; the
  container's request log shows `/oauth/github/start?<hidden>`. Next: `[specs]` M4 task 11; the
  new `[local-stack]` task (worker without `env_file: .env`) can go before or after. Gotchas:
  logins are lost when the gateway container is recreated; `docker compose config` prints every
  `.env` secret the worker inherits, so never paste its output.

- **2026-11-18 · tool-gateway** — M4 task 10: owner login for `acts_as: owner` through the team
  bot's GitHub App (ADR 0015) in `src/gateway/oauth/`: `signing.py` (HMAC notes with purpose and
  expiry; PKCE verifier derived from the nonce), `github.py` (exchange, refresh, `GET /user`),
  `logins.py` (login link, `begin` / `finish`, `headers` with refresh within 5 minutes of expiry
  or after a 401, refused refresh deletes the login), `routes.py` (`/oauth/github/start`,
  `/callback`; nonce cookie HttpOnly, SameSite=Lax; `no-store`, no referrer; 503 without
  settings). `Credentials(store, logins=)` handles the `oauth` kind; an owner run without a login
  gets a tool error naming the link. Settings `GITHUB_OAUTH_CLIENT_ID`, `GITHUB_OAUTH_CLIENT_SECRET`,
  `OAUTH_STATE_SECRET`, `GATEWAY_PUBLIC_URL`; `Settings.env` and the secrets are hidden from repr.
  `tests/fake_github_oauth.py` (checks client secret and PKCE, rotates refresh tokens); 36 tests in
  `tests/test_oauth.py`, 209 in all. Live: owner `add_label` on sandbox #9 first failed with the
  link, then after a browser login showed actor = the person, `performed_via_github_app` = the App.
  Next: `local-stack` passes the login settings (Cross-service changes), then `[specs]` M4 task 11.
  Gotchas: GitHub's App settings page moved things: the client secret is under **Credentials**,
  "Callback URL" is now **Redirect URI** (must be saved, or GitHub says "must be configured with a
  callback URL"), token expiry is under **Optional features**. uvicorn's request log printed the
  callback's `code` and `state`; `__main__.HideLoginQueries` hides `/oauth/` queries. The memory
  store forgets logins on restart. The Docker gateway must be stopped to run one on the host
  (both use 8090).

- **2026-11-18 · local-stack** — M4 task 9: `tool-gateway/Dockerfile` (uv, contracts and
  connectors copied in, `/health` check) and a `tool-gateway` compose service on 8090 that gets
  only the public run-token key, `CONNECTORS` (default `fake`), `GITHUB_REPOS`, `TENANT_ID` and
  the `GITHUB_APP_*` settings (`.pem` mounted read-only at `/run/secrets/github-app.pem` when set),
  never the whole `.env`. `make keys` (run by `make up` and every compose target) writes
  `.run-token-private.env` / `.run-token-public.env` at the repo root once (git- and
  docker-ignored; not under `local-stack/`, which is mounted into containers). The Docker worker is
  always `TOOLS_PROVIDER=mcp` with `GITHUB_TOKEN=""`. `demo.sh` also requires gateway access lines
  for the run. Checked: `make up && make demo` ("2 tool calls through the gateway"), `make chaos`
  passes twice (still the worker's `local` adapter against fake GitHub). Next: `[tool-gateway]`
  per-user OAuth for `acts_as: owner` (M4 task 10). Gotchas: compose reads only `.env` for
  `${...}`, so the gateway's key comes in through `env_file`; the fake and github connectors
  cannot load together (same tool names); the gateway's health check logs a `GET /health` line
  every 2 s.

- **2026-11-18 · worker** — M4 task 8: `adapters/tools_mcp.py` (`GatewayTools`, official MCP SDK
  client, one short session per request in its own event loop): run token as bearer, `_meta`
  step and idempotency key, annotations to `ToolSpec` flags, `structuredContent` (`{"result"}`
  unwrapped), `isError` / JSON-RPC errors / 401 / transport to `ToolError` without the token; no
  retries (core decides repeats, ADR 0013). Port change: `ToolContext(run_id, run_token, step,
  idempotency_key)` passed to `describe` and `call`; `RunRecord.run_token` (hidden from repr).
  `TOOLS_PROVIDER=mcp` + `TOOL_GATEWAY_URL`. Tests start the real gateway
  (`tests/live_gateway.py`, `uv run --project ../tool-gateway gateway`, fake connector,
  throwaway key): the `ToolClient` contract suite, 15 adapter tests, two `handle_run` runs. 312
  worker tests pass. Next: `[local-stack]` gateway in compose (M4 task 9). Gotchas: the SDK wraps
  errors in exception groups (`_first` unwraps); through `mcp` the GitHub tools take GitHub's
  argument names while `specs/issue-triage.yaml` still names the M1 ones (task 11); the MCP SDK
  adds starlette and uvicorn to the worker image; worker tests now need the gateway's `.venv`
  (`cd tool-gateway && uv sync`).

- **2026-11-18 · tool-gateway** — M4 task 7: second approval check. `approvals.py`
  (`ApprovalCheck`): a `require_approval` tool runs only if `GET /internal/runs/{sub}` shows the
  token's run and tenant, status `RUNNING`, and an `approved` approval with the call's step, tool
  and canonical arguments (same function as the worker). Anything else, a missing run or an
  unreachable control plane refuses; the model gets one generic text, the access log the reason.
  Port `ports/control_plane.py` with `adapters/control_plane_http.py` (retries 5xx, `signer` hook
  for M6 SigV4, unsigned locally) and `control_plane_memory.py`; contract suite;
  `tests/fake_control_plane.py`. `CONTROL_PLANE_URL` optional (unset: refused, startup warns).
  174 gateway tests pass; checked against the real local control plane (unknown run is 404, a real
  run's `approval` parses). Next: `[worker]` `tools_mcp.py` (M4 task 8). Gotchas: the check runs
  after the argument check, so invalid arguments never read the run; each GET makes the control
  plane issue a new run token, which the gateway ignores; the user chose SigV4 (in M6) over a
  bearer secret for gateway to control plane calls.

- **2026-11-18 · tool-gateway** — M4 task 6: team bot as a GitHub App. `github_app.py`
  (`AppCredential` {app_id, private_key, installation_id}; `GitHubAppTokens`: RS256 app JWT, 10
  minutes, backdated 60 s, exchanged at `/app/installations/{id}/access_tokens` on the connector's
  `base_url`, cached per API / app / installation until 5 minutes before expiry, exchange 5xx
  retried 3 times, 401/403/404 named as a rejected credential). `credentials.py` returns an `Auth`
  (headers plus `renew()`); the executor renews once on a GitHub 401. `seed.py` puts `TENANT_ID` +
  `GITHUB_APP_ID` / `GITHUB_APP_INSTALLATION_ID` / `GITHUB_APP_PRIVATE_KEY_FILE` into the memory
  `TokenStore` at startup (all or none). App `agent-triage-bot-dev` (Issues write, Metadata read)
  installed on the sandbox only. 137 gateway tests pass. Live: the real `uv run gateway` added
  `question` to sandbox #9, GitHub's event shows `agent-triage-bot-dev[bot]`; `tools/list` listed
  the sandbox labels. Next: `[tool-gateway]` second approval check (M4 task 7). Gotchas: the
  fake GitHub answers the token exchange apart (`token_requests`, `fail_token_next`), so
  `requests` holds only API calls; the App key stays outside the repo (`*.pem` is not in
  `.gitignore`); `pkill -f gateway` also kills the shell running it, stop by port instead.

- **2026-11-18 · tool-gateway** — M4 task 5: `connectors/github/hooks.py` with three hooks:
  `search_results_in_allowed_repos` (drops results outside `GITHUB_REPOS`, a second guard after
  the `repo:` qualifiers), `existing_labels_only` (exactly one string label; unknown name fails
  listing the existing ones; `describe` adds each allowed repo's labels to the tool description at
  `tools/list`, plain description kept if GitHub fails), `close_issue_checks` (reads first: pull
  requests refused, missing issue named, already closed returns `already_closed: true` without a
  second PATCH; `duplicate_of` only with `state_reason: duplicate`, not itself, turned into the
  original's issue id). Executor: `ApiError(status)` for 4xx, `HookCall.settings`, `DescribeCall`;
  `load_hooks` moved to `openapi_to_mcp`. `tests/test_github_parity.py` maps all 25
  `test_tools_local.py` tests to gateway tests. 114 gateway tests pass; no live GitHub call yet.
  Next: `[tool-gateway]` team bot as a GitHub App (M4 task 6; the user creates and installs the
  App first). Gotchas: the label description costs one GitHub call per allowed repo on every
  `tools/list`; add a worker test to `test_tools_local.py` and the parity map must follow.

- **2026-11-18 · tool-gateway** — M4 task 4: generator `tools/openapi_to_mcp.py` (allowlist checked
  against the contract plus 12 load-time rules, input schemas from the operation's own parameters
  with refs inlined and limits added), `tools/rules.py`, `tools/executor.py` (`ConnectorClient`,
  `HookCall`), interim `credentials.py` (stored `{"token"}` per tenant / `team-bot` or owner /
  connector). `connectors/github/`: `openapi.yaml` cut by `scripts/cut_openapi.py` from GitHub's
  official description (5 operations, ~120 KB), `allowlist.yaml` (search, add label, close;
  GitHub's own argument names). Contract: allowlist `headers`, `{"items"}` for list results.
  88 gateway tests and 96 contracts tests pass; no live GitHub call (needs task 6). Next:
  `[tool-gateway]` GitHub hooks with parity to `tools_local.py` (M4 task 5). Gotchas: GitHub's
  add-labels body is a `oneOf` (the generator takes the object variant; label items may still be
  objects, so the `existing_labels_only` hook must accept strings only); `state_reason` in
  GitHub's enum includes `reopened`, narrowed by the allowlist; `(?i)` must lead a Python regex;
  fake servers need `serve_forever(poll_interval=...)` small or each test waits 0.5 s.

- **2026-11-18 · tool-gateway** — M4 task 3: `tool-gateway/` scaffold. MCP SDK 2.3 low-level
  `Server` (handlers as `on_*` constructor arguments), Streamable HTTP stateless with JSON
  responses at `/mcp` behind `RunTokenAuth` (ASGI wrapper: token checked on every request, claims
  in the scope's `state`, 401 with a JSON-RPC error otherwise), `GET /health`. `tools/list` and
  `tools/call` limited to the token's tools; params checked against `mcp-tool-call.schema.json`,
  arguments against the tool's schema (SDK 2.x validates nothing); `require_approval` tools
  refused until task 7. Access log `gateway.access` (JSON per call, argument hash only).
  `TokenStore` port, memory adapter and contract suite; fake connector mirroring the worker's
  fakes. Starlette, not FastAPI (the SDK builds the app). Result convention (`structuredContent`)
  added to the MCP contract's description. 43 tests pass, lint clean; `uv run gateway` smoke
  checked; the Python `kid` thumbprint equals Nimbus's for the same key. Next: `[tool-gateway]`
  OpenAPI-to-MCP generator and the GitHub connector (M4 task 4). Gotchas: SDK 2.x renamed nearly
  everything (snake_case fields, `httpx2` client, `MCPError`); unexpected handler exceptions
  become a sanitized -32603, so model-readable failures must be `isError` results. macOS has no
  `timeout` command; start the server in the background and kill it.

- **2026-11-18 · control-plane** — M4 task 2: versions record `owner` and runs `started_by`
  (`tenant/Principals`: `api:<tenant>`, `webhook:github`; DynamoDB rows from before read as
  `api:<tenant>`); `token/RunTokenIssuer` signs an ES256 run token on every
  `GET /internal/runs/{id}` (Nimbus JOSE+JWT 10.3, `kid` = RFC 7638 thumbprint, `exp` = issue +
  `max_seconds`, claims validated against the contract in tests). Keys `RUN_TOKEN_PRIVATE_KEY` and
  `RUN_TOKEN_PUBLIC_KEY` (both: plain Java cannot derive the public key for `kid`; startup checks
  the pair); neither set = no token, so `make up` / `make demo` are unchanged. 256 tests pass
  (DynamoDB on Docker). Next: `[tool-gateway]` scaffold (M4 task 3). Gotchas: `.env` and
  docker `env_file` take one-line values, so keys go in as the base64 body (or PEM with `\n`);
  generate with the `openssl` commands in `control-plane/AGENTS.md`, checked to load. The
  gateway gets `RUN_TOKEN_PUBLIC_KEY` only and must compute the same thumbprint as `kid`.

- **2026-11-18 · contracts** — M4 task 1: `run-token.schema.json` (claims; ES256 and `kid`,
  per-GET issue, principals `kind:id`), `connector-allowlist.schema.json` (ADR 0014 format; its
  valid example is the planned GitHub connector: search, add label, close with hooks
  `search_results_in_allowed_repos`, `existing_labels_only`, `close_issue_checks`),
  `mcp-tool-call.schema.json` (`tools/call` params, `_meta` keys `agentplatform/step` and
  `agentplatform/idempotency_key`, conventions in its description); `run_token` in the internal
  API points to the claims schema. Decided with the user: tools expose GitHub's own parameter
  names (`owner`, `repo`, `q`, `labels`, `state_reason`), no renaming layer. 93 tests pass.
  Next: `[control-plane]` record `owner` / `started_by` and sign the run token (M4 task 2).
  Gotchas: schemas now `$ref` siblings by file name; a service validating them must register
  all `*.schema.json` files (see `REGISTRY` in `contracts/tests/test_schemas.py`). YAML values
  containing `: ` need quotes in allowlists.

- **2026-11-18 · contracts** — M4 planned (task list above, 11 tasks) with the user's choices: team
  bot is a GitHub App, `acts_as: owner` uses per-user OAuth in M4, every gateway call writes an
  access log with both identities, connector limits are allowlist rules plus named hooks (new
  ADR 0014), `TokenStore` on AWS is SSM Parameter Store (architecture updated). Draft lists for
  M5 to M8; M8's goal (Strands fixes upstream) no longer fits since the worker does not use
  Strands, decide when planning M8. RBAC added to open questions. No contract changed yet; 60
  contracts tests pass. Next: `[contracts]` run token, connector allowlist and MCP call
  conventions (M4 task 1). Gotchas: hooks run with a real credential, so each needs
  recorded-response tests; the parity check uses `worker/tests/unit/test_tools_local.py` cases.
  `contracts/.venv` was evicted by iCloud (tests hung); rebuilt with `rm -rf .venv && uv sync`.

- **2026-11-18 · specs** — `issue-triage.yaml` (version 4 in the local stack) adds
  `github.close_issue` under `require_approval`: after labeling, only a high-confidence duplicate,
  only the new issue, `reason: duplicate` with `duplicate_of`; summary gains `Closed: yes | no`;
  `max_steps` 20. Live in the sandbox: #8 (duplicate of #5) paused, Approve in Slack closed it as
  duplicate, run `SUCCEEDED` (13 steps, $0.0048); #9 (duplicate of #2) paused, Reject left it open,
  run `FAILED` `approval_rejected`. **M3 done.** Next: plan M4 (tool gateway) tasks. Gotchas:
  GitHub search has no stemming ("crash" misses "crashes"; the first run missed #5), so the
  instructions ask for short queries or `OR`. Some networks block Cloudflare quick tunnels by DNS
  (`api.trycloudflare.com` resolves to a block page; `cloudflared` times out); clicks came through ngrok with a
  traffic policy allowing only `POST /approvals/slack` (a tunnel exposes every route, and
  `/internal/*` has no auth locally; policy in `specs/README.md`). The Slack Request URL now
  points at a dead ngrok URL. `make up` rebuilt images; the old stack predated the Slack code.

- **2026-11-18 · control-plane** — Approval expiry: `ApprovalSweeper.expireOnce()` (same sweep
  schedule) fails runs left `WAITING_APPROVAL` longer than `APPROVAL_TIMEOUT_HOURS` (default 72,
  from the start of the current pause; startup fails outside 1 to 359, under the worker's 15-day
  retention) with `approval_expired`. New `RunStore` methods: `findWaitingApprovalsBefore` (both
  `RUNS#APPROVAL_*` partitions, merged oldest first, 20 per sweep) and `expireApproval`
  (conditional on the same pending call, like `decideApproval`; clears the pending call, leaves
  `approval` unset). Posted messages show "Expired" (`ApprovalOutcome.expired`, `chat.update`); a
  failed update keeps the expiry and the rest of the sweep skips Slack; a late click shows
  "Expired". The cross-service entry for the approval decision is closed (both services follow).
  238 tests pass (DynamoDB on Docker). No live Slack check (whole hours; unit tests per the task).
  Next: `[specs]` issue-triage closes confirmed duplicates under `require_approval` (last M3
  task; needs a fresh cloudflared URL in the Slack app's Request URL). Gotchas: the expiry race
  test runs a decision and an expiry together on both stores; any new way out of
  `WAITING_APPROVAL` needs the same.

- **2026-11-18 · control-plane** — `POST /approvals/slack`: `SlackInteractions` verifies the
  signature (Slack's documented example is a test vector) and reads Approve / Reject clicks;
  `ApprovalClicks` decides through `RunService.decideApproval` (`decided_by` =
  `slack:<user id>`, anyone in `SLACK_APPROVAL_CHANNEL` may decide, other channels ignored) and
  shows the outcome on the clicked message (`ApprovalChannel.showOutcome`, Slack `chat.update`,
  1.5 s timeout; failure keeps the decision). 216 tests pass. Real clicks in the user's channel
  through a Cloudflare quick tunnel approved and rejected one run each. Next: `[control-plane]`
  approval expiry (read both `RUNS#APPROVAL_UNSENT` and `RUNS#APPROVAL_SENT`; show the expiry
  on the message with `showOutcome`). Gotchas: the first real click got 401 because
  `@RequestBody byte[]` on a form POST is rebuilt by Spring with Java's encoding; read the
  servlet input stream. smee.io was replaced by `cloudflared tunnel --url` (smee re-sends bodies
  as JSON); the quick tunnel URL changes on every start, so the Slack Request URL must be
  updated (it now points at a dead tunnel). Click fixture `block_actions.json` is hand-made from
  Slack's documented example, not recorded.

- **2026-11-17 · control-plane** — Slack `ApprovalChannel` (package `approval/`): a worker's
  `WAITING_APPROVAL` PATCH posts the pending call (`chat.postMessage`, Approve / Reject buttons
  valued `{run_id, step}`, arguments as literal preformatted text, max 3,000 characters) and
  saves channel and message ts on the run (`markApprovalSent`, conditional on the same pending
  call). A failed post never fails the PATCH; the approval sweep (same schedule) posts runs
  unsent for 60 s, 20 per sweep, stopping at the first failure. Index: `RUNS#APPROVAL_UNSENT` /
  `RUNS#APPROVAL_SENT` on GSI1 while waiting (expiry task reads both); rejections now remove
  GSI1 keys. No `SLACK_BOT_TOKEN` = requests only logged. Fixtures recorded from the user's own
  Slack workspace (app "Agent Approvals", `chat:write`, interactivity off). 182 tests pass; a
  real message appeared in the channel from a local `bootRun`. Next: `[control-plane]`
  `POST /approvals/slack` (turn on Interactivity with the smee.io URL; signing secret already in
  `.env`; `update_message.json` fixture already recorded for showing who decided). Gotchas:
  Slack answers 200 with `ok: false` for errors; a fake token gave `internal_error`; mrkdwn
  cannot escape a closing code fence, so model text goes in `rich_text_preformatted`; Slack
  accepted 12,000 characters there, refused 40,000 (`msg_blocks_too_long`); python.org Python has
  no CA bundle (use the worker venv with `certifi` for recording scripts).

- **2026-11-17 · worker** — `github.close_issue` in `tools_local.py` (`reason` `completed`,
  `not_planned` or `duplicate`; optional `duplicate_of`; `idempotent`, not `read_only`). It reads
  the issue first: an already closed issue is reported (`already_closed: true`) without a write,
  pull requests and missing issues are refused; `duplicate_of` needs reason `duplicate`, must be
  another issue in the same repo, and is sent as `duplicate_issue_id` (the issue's id, looked
  up, not its number). No comment is posted. Fixtures recorded from the sandbox (throwaway issue
  #7, now closed; planted duplicates #4 and #6 untouched). 288 tests pass. Next:
  `[control-plane]` Slack `ApprovalChannel` (needs a free Slack workspace and app: give exact
  setup steps). Gotchas: recorded, a second PATCH on a closed issue answers 200 and overwrites
  its `state_reason`, so the read-first check is what makes the tool idempotent; GitHub's PATCH
  reply does not show the duplicate link; `/issues/{n}` also serves pull requests.

- **2026-11-17 · worker** — Resume after approval: `RunRecord.approval` (`ApprovalDecision`); in
  `_after_turn` a `require_approval` call runs (through `_execute`, idempotency key as usual)
  only if the decision is `approved` and step (steps + 1), tool and arguments (canonical JSON:
  `42` != `42.0`, `true` != `1`) match; anything else pauses again with the same pending call.
  `InMemoryControlPlane.decide()` mimics the control plane for tests. 274 tests pass (pause,
  approve, resume once; crash after the approved call reuses its result; no or mismatched
  decision pauses again). Next: `[worker]` `github.close_issue`. Gotchas: the repo is in
  iCloud-synced `~/Desktop` with "Optimize Mac Storage": macOS evicts `.venv` and cache files
  (2,406 in `worker/.venv`), and reads then block on download, so tests hang at 0% CPU (stack
  in `read()` of a `.pyc`). Check with `find .venv -flags +dataless | wc -l`; fix by
  `rm -rf .venv && uv sync`, or move the repo out of iCloud.

- **2026-11-17 · control-plane** — Approval decisions: `POST /runs/{id}/approval` (API key, body
  `{decision, step}`; `step` must equal the pending call's step, else 409). One conditional
  write (`RunStore.decideApproval`, on status `WAITING_APPROVAL` and the exact pending call
  text) sets `approval` (copied from the pending call; `decided_by` = `api:<tenant>`), clears
  `pending_approval`, and moves the run to `QUEUED` (enqueued; a failed enqueue still answers
  200, the sweep sends it) or `FAILED` `approval_rejected`. Second decision, run not waiting:
  409. Both run views return `approval`. 145 tests pass (8 store contract tests on both
  adapters, `ApprovalApiIntegrationTest`). Next: `[worker]` resume after approval. Gotchas:
  `RunStatus.allowedFrom()` is still the worker's table only; control-plane moves out of
  `WAITING_APPROVAL` go through `decideApproval`. Gradle cache `resourceHashesCache.bin` can
  break like `executionHistory.bin` (fix in `control-plane/AGENTS.md`).

- **2026-11-17 · contracts** — M3 planned (task list above). Internal API gains `approval`
  (`ApprovalDecision`: `step`, `tool`, `arguments`, `decision` approved|rejected, `decided_by`,
  `decided_at`; latest decision only) on `RunFields`, so GET returns it; `RunUpdate` still
  rejects it (workers never decide). Failure reason `approval_expired` added; `approval_*`
  reasons are control-plane only. 11 new contract tests; worker and control-plane suites pass
  unchanged. Next: `[control-plane]` approval decisions. Gotchas: the worker must compare
  arguments as JSON values, not strings; `approval` stays on the run after resume (the step
  number tells the worker whether it is for the current pause).

- **2026-11-17 · local-stack** — `make chaos` (`local-stack/chaos.sh`): fake GitHub API
  (`fake-github.py`, stdlib, compose profile `chaos`, port 8099, search delayed 8 s, request
  counts at `/_requests`), chaos worker override (`chaos.compose.yml`: real GitHub tools at the
  fake, dummy token, `RUN_VISIBILITY_SECONDS=10`), agent `chaos-demo` (`chaos-spec.yaml`, stub
  model). SIGKILLs the worker during the search, restarts it, checks SUCCEEDED, 5 steps, 2
  searches, 1 add-label. Passed twice on a fresh stack. `create-tables.sh` also enables TTL on
  the control-plane table. `make demo` still passes (fake tools). **M2 done.** Next: plan M3 tasks
  (approvals, Slack) in this file before starting them. Gotchas: replacing the worker container
  leaves its last 20 s long poll open at ElasticMQ, and a message sent then is hidden for the old
  visibility timeout (chaos waits 21 s; affects any worker swap); the kill lands in the read-only
  search on purpose: a kill inside add-label repeats the request (idempotent, ADR 0013), so the
  label is set once but GitHub sees two requests; `make down` now also stops `fake-github`; the
  stack was reset, real `issue-triage` republished as version 2.
- **2026-11-17 · control-plane** — Webhook redelivery dedupe. `DeliveryStore` port
  (in-memory, DynamoDB; contract suite): `claim` (conditional put, `DELIVERY#<source>#<id>`,
  `ttl` 7 days, expired item counts as absent), `recordRuns`, `release`. Controller claims
  `X-GitHub-Delivery` after the signature check; duplicate: 200 `{runs, duplicate: true}`;
  failure releases the claim except `RunQueueException`; no header: handled, warning. Also fixed
  the flaky all-404 integration tests: test server binds `127.0.0.1` (`SERVER_ADDRESS` in
  `build.gradle.kts`), clients call `127.0.0.1` (a Gradle daemon held the same random port on
  `::1`; seen 3 of ~11 runs before, 0 of 6 after). Tests: 120 passed (15 new). Live: same signed
  delivery twice: 202 then 200 duplicate with the same run id. Next: `[local-stack]` `make
  chaos` (and enable the control-plane TTL, see Cross-service changes). Gotchas: local stack has
  `sweep-demo` (stub, trigger `github.issue.locked`) from live checks.
- **2026-11-17 · control-plane** — Re-enqueue sweep. Sparse index on GSI1 (`RUNS#QUEUED`,
  13-digit epoch-millis queued-at) set while `QUEUED`, removed on any other status;
  `RunStore.findQueuedBefore` / `touchQueued` (both adapters, contract tests). `RunSweeper`
  touches then publishes each run `QUEUED` > `SWEEP_QUEUED_AFTER_SECONDS` (300), up to 1,000 per
  sweep; `RunSweepSchedule` every `SWEEP_INTERVAL_SECONDS` (60, 0 = off; tests run with 0).
  Tests: 105 passed (16 new). Live: ElasticMQ stopped, `POST /runs` 503, queue restarted, the
  run reached SUCCEEDED ~15 s later via the sweep (5 s / 10 s settings, stub agent `sweep-demo`
  left in the local stack). Next: `[control-plane]` webhook dedupe. M6: add an EventBridge
  Scheduler rule that calls `sweepOnce` and set `SWEEP_INTERVAL_SECONDS=0` on Lambda. Gotchas:
  `GSI2SK` uses `Instant.toString()`, which misorders runs within one second (fix when an
  agent's run list is built); the all-404 test flake is fixed (see the dedupe entry); returning to `QUEUED` (approvals,
  M3) re-adds the index key in `update`, untested until that transition exists.
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
