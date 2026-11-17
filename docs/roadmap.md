# Roadmap

Current status, milestones and next tasks. Update this file as work moves; the other docs describe
the target design and should change only when the design does.

**Status:** M0 in progress. Schemas done; control plane publishes agents and versions; local
stack (DynamoDB Local + ElasticMQ) runs with `make up`; internal run API contract written.

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

| #   | Milestone      | Done when                                                                                  | Status      |
| --- | -------------- | ------------------------------------------------------------------------------------------ | ----------- |
| M0  | Local skeleton | `docker compose up`; publish a spec; one run completes end to end with the stub model      | In progress |
| M1  | Real agent     | Issue-triage agent runs on Bedrock and calls GitHub directly from the worker (personal token) | Not started |
| M2  | Crash safety   | Lease, checkpoints, idempotency; killing a worker mid-run causes no duplicate tool call     | Not started |
| M3  | Approvals      | Close-issue pauses; Slack buttons resume or reject the run                                 | Not started |
| M4  | Gateway        | Tool calls move into the gateway; OpenAPI → MCP generator; OAuth and team bots             | Not started |
| M5  | Evals          | Suite runs in GitHub Actions and blocks a PR that lowers success                           | Not started |
| M6  | AWS deploy     | Terraform deploys everything scale-to-zero (ADR 0007): Lambdas behind API Gateway on a custom domain, workers at 0; traces in X-Ray; no hourly cost when idle | Not started |
| M7  | Scale proof    | 1,000 concurrent runs on the stub model; chaos test report in `loadtest/report.md`         | Not started |
| M8  | Open source    | Bugs found in the Strands Agents SDK reported and fixed upstream                           | Not started |

## MVP scope (M0 + M1)

The MVP is one real agent run end to end on a laptop: publish the issue-triage spec, start a run by
hand, and watch a Bedrock model label a real GitHub issue.

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
- [ ] `[control-plane]` `RunStore` port (in-memory + DynamoDB, contract suite), `POST /runs`,
      `GET /runs/{id}`, `RunQueuePublisher` (SQS + in-memory), internal
      `GET/PATCH /internal/runs/{id}`. Check: run item is `QUEUED` and a message is on the queue.
- [ ] `[worker]` `core/handle_run.py`, ports, in-memory adapters, stub model, one fake tool,
      budget (steps and seconds). Check: integration test runs a spec to `SUCCEEDED` with no
      network.
- [ ] `[worker]` `queue_sqs.py`, `control_plane_http.py`, `poll_loop.py`, `config.py`, `wiring.py`.
      Check: with the stack up, a run started by curl reaches `SUCCEEDED`.
- [ ] `[local-stack]` add both services to compose, `make demo` script, README quickstart.
      Check: fresh clone → `make up && make demo` passes. **M0 done.**

## M1 tasks: real agent

- [ ] `[worker]` `adapters/model.py` with the Strands Bedrock provider, `core/pricing.py`, dollar
      budget. Check: stub tools, real model, run succeeds and reports cost.
- [ ] `[worker]` `tools_local.py`: `github.search_issues`, `github.add_label` with a personal
      token. Check: unit tests with recorded responses.
- [ ] `[specs]` final `issue-triage.yaml`. Check: a manual run against a test repo labels a real
      issue and finds a planted duplicate. **M1 done: MVP.**
- [ ] `[control-plane]` (optional) `POST /triggers/github` with HMAC check; deliver webhooks
      locally with smee.io. Check: opening an issue starts a run.

## Cost guardrails

- Set an AWS Budgets alert before deploying anything.
- Nothing billed per hour (ADR 0007): no NAT gateway, load balancer, RDS, interface VPC endpoints
  or always-on instance. Public subnets for Fargate tasks; Lambdas outside the VPC.
- Before sharing the URL with others: API Gateway route throttling, per-run budgets enforced, and a
  daily Bedrock spend alarm.
- Use the cheapest capable Bedrock model for evals; use recorded fixtures so evals never call real
  APIs.
- Workers scale to 0 when idle; run load tests only against the stub model.

## Open questions

- Will the repo be public? Affects how much personal context belongs in it.
- GitHub App vs per-user OAuth for the first GitHub connector.
- Is the worker's scale-from-0 delay (a minute or more) acceptable for demos, or add the Lambda
  worker entrypoint (ADR 0005) early?
- Is Spring Boot's cold start on Lambda with SnapStart fast enough for Slack's 3-second callback
  limit, or does the control plane need a lighter framework?
- Can Strands hooks checkpoint every step, or does the worker need its own loop wrapper?
- k6 or Locust for the load driver.
- Gateway `TokenStore`: Secrets Manager costs $0.40 per secret per month, which grows with every
  stored OAuth token. SSM Parameter Store SecureString (standard tier, free) may be the better
  adapter. Decide in M4.

## Cross-service changes

Changes to `contracts/` that another service still has to adopt. Remove an entry once every listed
service has followed.

- **Internal API and gateway auth is IAM (SigV4) through API Gateway** (ADR 0007). Declared in
  `contracts/internal-api.openapi.yaml`. Must follow: `worker` (`control_plane_http.py`, later
  `tools_mcp.py` sign requests), `tool-gateway` (signs calls to the control plane).
- **Internal run API added** (`contracts/internal-api.openapi.yaml`): `GET /internal/runs/{run_id}`
  (run + pinned spec + `input`, `run_token` from M4) and `PATCH /internal/runs/{run_id}` (status,
  `steps`, `cost_usd`, `trace_id`, `pending_approval`, `failure`, `output`; allowed status changes
  in the file's description, others 409). snake_case JSON. Must follow: `control-plane` (implement
  both endpoints and the transition rules), `worker` (`control_plane_http.py`).
- **Agent spec schema added** (`contracts/agent-spec.schema.json`). No `version` field: the control
  plane assigns version numbers. `require_approval ⊆ tools` is not in the schema and must be checked
  in code. Must follow: `worker` (reads the spec fields it needs). `control-plane` done.
- **Run message schema added** (`contracts/run-message.schema.json`): `{schema_version: 1, run_id,
  tenant_id}`. Must follow: `control-plane` (`RunQueuePublisher` sends it), `worker` (`queue_sqs.py`
  parses it).

## Handoff notes

Newest first. One entry per work session: date, component, done, next, gotchas. Trim entries older
than the current milestone.

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
