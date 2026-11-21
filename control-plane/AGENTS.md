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
| Lambda zip | `./gradlew lambdaZip` (`build/distributions/control-plane-lambda.zip`, ADR 0017) |
| Live check against dev AWS (one KMS signature, one SSM read; ~$0) | `LIVE_AWS=1 ./gradlew test --tests '*LiveAwsCheckTest'` (profile `default`; prints no secret) |
| Lambda trial (AWS, about 5 min per memory size, ~$0) | `uv run --with boto3 python scripts/lambda_trial.py --configs 1769:on` (makes and deletes its own function, table, role; profile `default`) |
| Tenants on a table (AWS, profile `default`) | `./gradlew tenantAdmin --args="create <tenant> [--max-running N] [--daily-usd X] --table agent-platform-dev-control-plane"` (also `add-key`, `set-limits`, `show`; prints a new key once: send output straight to a 0600 file outside the repo, never to a log) |
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
`token_rotation_enabled: false`; Install to Workspace, copy the `xoxb-` Bot User OAuth Token and
the Signing Secret (Basic Information, `SLACK_SIGNING_SECRET`), run `/invite @<app>` in the
channel, take its Channel ID from the channel details. Try it (port 8080 is taken by `make up`):

```sh
set -a && . ../.env && set +a && STORE_BACKEND=memory QUEUE_BACKEND=memory \
  TENANT_API_KEY=local-dev-key SERVER_PORT=18080 ./gradlew bootRun
cloudflared tunnel --url http://127.0.0.1:18080   # brew install cloudflared; no account
```

Paste `https://<name>.trycloudflare.com/approvals/slack` into the app's Interactivity & Shortcuts →
Request URL (a new URL on every tunnel start; DNS can take a minute). Start a run, PATCH it
`RUNNING` then `WAITING_APPROVAL` with a `pending_approval`, click in Slack. Use cloudflared, not
smee.io: smee re-sends bodies as JSON, and Slack's form body must arrive byte for byte.

Run tokens (`GET /internal/runs/{id}` → `run_token`): a P-256 key pair, both halves in `../.env`
(one line each: the base64 body, or PEM with `\n`):

```sh
openssl ecparam -name prime256v1 -genkey -noout | openssl pkcs8 -topk8 -nocrypt -out run-token.pem
openssl ec -in run-token.pem -pubout -out run-token.pub.pem
echo "RUN_TOKEN_PRIVATE_KEY=$(grep -v -- ----- run-token.pem | tr -d '\n')"      # secret
echo "RUN_TOKEN_PUBLIC_KEY=$(grep -v -- ----- run-token.pub.pem | tr -d '\n')"
```

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
  `internal`, `store`, `queue`, `contract`, `tenant`, `config`, `web`, `approval`, `token`,
  `secret`, `lambda`, `trace`).
- Webhook routes (`/triggers/*`) have no API key; they trust only a valid signature. Keep the
  verification and payload rules in plain classes (`GitHubWebhooks`) so they test without HTTP.
- Webhook deliveries are claimed in `DeliveryStore` before any run starts; tests send a unique
  `X-GitHub-Delivery` per request, or the second request is a duplicate.
- Ports: `DeliveryStore` joins `AgentStore` / `RunStore` (contract suite `DeliveryStoreContractTest`);
  `TenantStore` (`TenantStoreContractTest`) holds tenant records and API keys by SHA-256.
- API keys (ADR 0026): `ApiKeyAuthFilter` checks the configured `TENANT_API_KEY` first, then
  stored keys by hash, only for keys shaped like `ApiKeys`' (`ap_` + 43 base64url). Never log a
  key or its hash; errors log the exception type only. Limits come from
  `TenantDirectory.limits(tenant)` (record, else `TENANT_MAX_RUNNING` / `TENANT_DAILY_USD`).
- Tenant limits (ADR 0026-0028): slots change only inside `RunStore` writes that change status
  (never a separate counter call); a new way into or out of RUNNING must take or free the slot in
  the same write and get a test in `RunStoreContractTest`. Starts go through `RunService.update`
  (cap, daily spend); `RunLimits` holds the rules and delays. Delayed re-queues set queued-at to
  when the message is due (`touchQueued(runId, now + delay)` or `setAside`). The sweep pass is
  re-enqueue, approval post, approval expiry, stalled runs (`Sweeps.Result`).
- `RunQueuePublisher` is `publish(message, delaySeconds)` (0-900; `publish(message)` = 0); test
  lambdas take both arguments. The SQS adapter always sends `MessageGroupId` = tenant.
- Priming sends no `X-Api-Key` unless `peek` holds the key: any other key would make the
  filter read SSM before the snapshot.
- Tests bind the server to `127.0.0.1` (`SERVER_ADDRESS` in `build.gradle.kts`) and call
  `127.0.0.1`, never `localhost`: `localhost` can resolve to `::1`, where another process (a Gradle
  daemon) may hold the same random port; symptom: every request 404 or "Invalid status line".
- Ports: `AgentStore`, `RunStore` (in `store/`), `RunQueuePublisher` (in `queue/`),
  `ApprovalChannel` (in `approval/`), `RunTokenSigner` (in `token/`), `SecretSource` (in `secret/`). Each port has
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
- Secrets (ADR 0018): never keep a copy in a field or read one from `PlatformProperties`; ask
  `SecretSource.get(Secret.X)` on each use (SSM on AWS loads on first use). A new secret: add it to
  `Secret`, to the env map in `SecretConfig`, and as a parameter in `infra/envs/dev/ssm.tf`. Code
  that runs before the SnapStart snapshot (priming) uses `peek` only. AWS fakes for tests:
  `FakeSsm`, `FakeKms` (implement the SDK client interfaces; no Docker).
- Config comes from env vars via `application.yml` (`STORE_BACKEND`, `CONTROL_PLANE_TABLE`,
  `QUEUE_BACKEND`, `RUN_QUEUE_URL`, `TENANT_ID`, `TENANT_API_KEY`, `TENANT_MAX_RUNNING`,
  `TENANT_DAILY_USD`, `TENANT_CAP_RETRY_SECONDS`, `GITHUB_WEBHOOK_SECRET`,
  `SWEEP_INTERVAL_SECONDS`, `SWEEP_QUEUED_AFTER_SECONDS`, `SWEEP_RUNNING_STALLED_AFTER_SECONDS`, `SLACK_BOT_TOKEN`,
  `SLACK_APPROVAL_CHANNEL`, `SLACK_API_URL`, `SLACK_SIGNING_SECRET`, `APPROVAL_TIMEOUT_HOURS`, `RUN_TOKEN_PRIVATE_KEY`, `RUN_TOKEN_PUBLIC_KEY`,
  `AWS_ENDPOINT_URL_DYNAMODB`, `AWS_ENDPOINT_URL_SQS`, `SECRETS_SOURCE`, `SECRETS_SSM_PATH`,
  `RUN_TOKEN_KMS_KEY_ID`, `TRACING_EXPORTER`). No network calls at startup (Lambda SnapStart).
- Lambda (ADR 0017): `lambda/LambdaHandler` is the entry; Spring starts in its static
  initializer, before the SnapStart snapshot, and `lambda/Priming` sends network-free requests
  before it. A snapshot is shared by every restored environment: make ids, secrets and seeds only
  with `SecureRandom`, never in static or startup code with `Random`. Scheduled work for Lambda
  goes into `run/Sweeps.runOnce()` (the `{"sweep": "all"}` event); a new priming request must end
  before any store, queue or Slack call. Keep the `ServerlessAutoConfiguration` exclusion in
  `application.yml` (without it Tomcat is replaced and local runs and integration tests fail).
  Trial results land in `build/lambda-trial/`; Lambda redelivers late log lines, so a log group
  deleted right after its function can come back.
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
- Signed form bodies (`/approvals/slack`): read `HttpServletRequest.getInputStream()`, never
  `@RequestBody byte[]` (Spring rebuilds form POST bodies from parsed parameters, re-encoded, so
  real Slack signatures fail while Java-encoded test bodies pass). Tests send Slack-style
  encoding (`%20`, unencoded `:`).
- The control plane ends a wait in one of three ways, each conditional on the exact pending call:
  `decideApproval` (approved / rejected, sets `approval`) or `expireApproval` (`approval_expired`,
  no `approval`). A new way out of `WAITING_APPROVAL` gets the same condition and a race test
  against `decideApproval` in `RunStoreContractTest`.
- Run tokens (`token/RunTokenIssuer`): never log a token, a key or a key's text in an error;
  tests generate throwaway keys with `TestKeys`. Identities are `<kind>:<id>` from
  `tenant/Principals` (`api:<tenant>`, `webhook:github`); a new trigger adds its own constant.
- Posting approval requests never fails the worker's PATCH (`ApprovalNotifier` returns an
  outcome, does not throw); a missed post is the approval sweep's job.
- Slack fixtures (`src/test/resources/fixtures/slack/`) are recorded response bodies with
  workspace, user, bot and app ids and bot profile removed, channel `C0123456789`; tests replay
  them with `FakeSlackServer`. Recording posts to the real channel: delete probe messages after.
- Tracing (ADR 0022): `TRACING_EXPORTER=none` (default, and on Lambda: spans and trace context are
  made, nothing is uploaded) or `logging` (spans in the log; try `TRACING_EXPORTER=logging
  ./gradlew bootRun`). A run's trace starts in `RunService.start` ("run.start", no parent);
  control-plane work on an existing run uses `RunTracing.startInRun(run, name)` +
  `RunTracing.inSpan` and sends `RunTracing.traceParent(span)` on any queue message. Span
  attributes: ids only (`tenant.id`, `agent.name`, `agent.version`, `run.id`, route, status);
  never input, output, headers, tokens or exception messages. A new AWS client gets
  `overrideConfiguration(c -> c.addExecutionInterceptor(awsSdkTracing))`. Ids come from
  `SecureIdGenerator` (snapshot-safe). Tests: `trace/TestTracing` (real tracer, spans in memory) or
  a `@Primary TraceExport` over an `InMemorySpanExporter` (`TracingIntegrationTest`);
  `RunTracing.noop()` where spans do not matter. `InMemoryRunQueuePublisher.messages()` returns
  messages with their `traceParent`. Do not turn on Lambda active tracing (+250 ms cold start).
