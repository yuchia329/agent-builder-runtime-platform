# control-plane

Java 21, Spring Boot 4, Gradle (Kotlin DSL). Design: `docs/architecture.md` → Control plane.

## Commands

Run from `control-plane/`.

| What | Command |
| --- | --- |
| Test (all) | `./gradlew test` (Docker must be running for the DynamoDB suite) |
| Test without Docker | `./gradlew test --tests '*InMemory*' --tests '*Validator*' --tests '*Api*' --tests '*ServiceTest'` |
| Run locally | `./gradlew bootRun` (profile `local`, in-memory store, API key `local-dev-key`, port 8080) |
| Build jar | `./gradlew bootJar` |
| Image (from repo root) | `docker build -f control-plane/Dockerfile .` (or `make up`) |

Try it:

```sh
curl -X POST localhost:8080/agents -H 'X-Api-Key: local-dev-key' \
  -H 'Content-Type: application/json' -d '{"name":"issue-triage"}'
curl -X POST localhost:8080/agents/issue-triage/versions -H 'X-Api-Key: local-dev-key' \
  -H 'Content-Type: application/yaml' --data-binary @../contracts/examples/agent-spec.valid.yaml
curl -X POST localhost:8080/runs -H 'X-Api-Key: local-dev-key' \
  -H 'Content-Type: application/json' -d '{"agent":"issue-triage","input":{"issue":{"number":42}}}'
curl localhost:8080/internal/runs/<run_id>          # no API key on /internal/*
```

GitHub webhooks (`POST /triggers/github`, needs `GITHUB_WEBHOOK_SECRET`): sign a test delivery by
hand, or forward real ones from a repo webhook (content type `application/json`, event Issues,
same secret) through smee.io:

```sh
body='{"zen":"hi"}'
sig="sha256=$(printf '%s' "$body" | openssl dgst -sha256 -hmac "$GITHUB_WEBHOOK_SECRET" -hex | sed 's/.*= //')"
curl localhost:8080/triggers/github -H 'X-GitHub-Event: ping' -H "X-Hub-Signature-256: $sig" \
  -H 'Content-Type: application/json' -d "$body"
npx smee-client --url https://smee.io/<channel> --target http://localhost:8080/triggers/github
```

Slack approvals (`SLACK_BOT_TOKEN`, `SLACK_APPROVAL_CHANNEL` in `../.env`; unset = only logged):
create an app at api.slack.com/apps (From scratch, Paste a manifest) with bot scope `chat:write`,
`token_rotation_enabled: false` and interactivity off until `/approvals/slack` exists; Install to
Workspace, copy the `xoxb-` Bot User OAuth Token and the Signing Secret (Basic Information), run
`/invite @<app>` in the channel, take its Channel ID from the channel details. Try it: start with
`set -a && . ../.env && set +a && STORE_BACKEND=memory QUEUE_BACKEND=memory ./gradlew bootRun`,
start a run, PATCH it `RUNNING` then `WAITING_APPROVAL` with a `pending_approval`.

Against the local stack (DynamoDB + SQS) instead of memory: `make stack` at the repo root (`make
up` also starts a control-plane container on 8080), then
`set -a && . ../.env && set +a && ./gradlew bootRun`.

## JDK

No system JDK 21 needed. `gradle/gradle-daemon-jvm.properties` makes Gradle download Temurin 21
into `~/.gradle/jdks` and run the build on it; the `./gradlew` launcher works on any Java 8+.
Keep Gradle on 8.14.x while machines may only have Java 11 (Gradle 9 needs Java 17 to launch).

If a build fails with `Could not open cache executionHistory.bin` (`EOFException`), a crashed
daemon left the cache cut short: `./gradlew --stop && rm -rf .gradle/*/executionHistory`, then
build again. Same fix for `Could not open cache resourceHashesCache.bin`: stop the daemon and
remove `.gradle/*/fileHashes` (and `executionHistory`).

## Conventions

- Package `dev.agentplatform.controlplane`, one package per area (`agent`, `run`, `trigger`,
  `internal`, `store`, `queue`, `contract`, `tenant`, `config`, `web`).
- Webhook routes (`/triggers/*`) have no API key; they trust only a valid signature. Keep the
  verification and payload rules in plain classes (`GitHubWebhooks`) so they test without HTTP.
- Webhook deliveries are claimed in `DeliveryStore` before any run starts; tests send a unique
  `X-GitHub-Delivery` per request, or the second request is a duplicate.
- Ports: `DeliveryStore` joins `AgentStore` / `RunStore` (contract suite `DeliveryStoreContractTest`).
- Tests bind the server to `127.0.0.1` (`SERVER_ADDRESS` in `build.gradle.kts`) and call
  `127.0.0.1`, never `localhost`: `localhost` can resolve to `::1`, where another process (a Gradle
  daemon) may hold the same random port; symptom: every request 404 or "Invalid status line".
- Ports: `AgentStore`, `RunStore` (in `store/`), `RunQueuePublisher` (in `queue/`),
  `ApprovalChannel` (in `approval/`). Each port has
  an abstract `*ContractTest`; every adapter has a subclass of it. New adapters must pass it.
  DynamoDB tests share one container through `DynamoDbLocal`.
- Shapes from `contracts/` are checked with `ContractValidator` against the bundled files, never
  re-typed in Java. Integration tests validate responses against the contract too.
- Run status rules live only in `RunStatus.allowedFrom()`, mirroring the contract's table (worker
  changes). Control-plane moves out of `WAITING_APPROVAL` go only through
  `RunStore.decideApproval` (conditional on status and the exact pending call).
- JSON is snake_case with nulls omitted (global Jackson setting in `application.yml`).
- DynamoDB adapters use the plain `DynamoDbClient` (not the Enhanced Client). Key helpers and the
  item layout are documented at the top of each adapter.
- The table's keys and indexes live only in `dynamodb/control-plane-table.json`. Tests create
  tables from it (`ControlPlaneTable`); change the table there, never in code.
- The agent spec schema is copied from `../contracts/` into the jar at build time; never keep a
  second copy.
- Jackson 3 (`tools.jackson.*`), not Jackson 2 (`com.fasterxml.jackson.*`).
- Config comes from env vars via `application.yml` (`STORE_BACKEND`, `CONTROL_PLANE_TABLE`,
  `QUEUE_BACKEND`, `RUN_QUEUE_URL`, `TENANT_ID`, `TENANT_API_KEY`, `GITHUB_WEBHOOK_SECRET`,
  `SWEEP_INTERVAL_SECONDS`, `SWEEP_QUEUED_AFTER_SECONDS`, `SLACK_BOT_TOKEN`,
  `SLACK_APPROVAL_CHANNEL`, `SLACK_API_URL`,
  `AWS_ENDPOINT_URL_DYNAMODB`, `AWS_ENDPOINT_URL_SQS`). No network calls at startup (Lambda SnapStart).
- Errors: throw domain exceptions; `web/ApiExceptionHandler` maps them to problem+json.
- Scheduled work is a plain class with a `...Once()` method (`RunSweeper.sweepOnce`) plus a thin
  `@Scheduled` trigger that is off when its interval is 0. Tests run with
  `SWEEP_INTERVAL_SECONDS=0` (set in `build.gradle.kts`); a test that needs the schedule sets it.
- Time in DynamoDB sort keys: zero-padded epoch millis, never `Instant.toString()` (it drops
  trailing zeros, so text order breaks within a second). `GSI2SK` still uses `toString()`.
- A class with two constructors needs `@Autowired` on the one Spring should use.
- `InMemoryRunQueuePublisher.bodies()` returns a snapshot; read it again when polling.
- Slack answers HTTP 200 for errors (`{"ok": false, "error"}`): check `ok`, never the status
  alone, and do not branch on error names (a bad token gave `internal_error`). Model-chosen text
  goes in `rich_text_preformatted` (shown literally), never in mrkdwn.
- Posting approval requests never fails the worker's PATCH (`ApprovalNotifier` returns an
  outcome, does not throw); a missed post is the approval sweep's job.
- Slack fixtures (`src/test/resources/fixtures/slack/`) are recorded response bodies with
  workspace, user, bot and app ids and bot profile removed, channel `C0123456789`; tests replay
  them with `FakeSlackServer`. Recording posts to the real channel: delete probe messages after.
