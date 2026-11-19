# 0017. Control plane on Lambda: serverless-java-container, SnapStart with priming, 1769 MB arm64

- Status: Proposed
- Date: 2026-11-19

## Context

ADR 0007 puts the control plane on AWS Lambda (Java 21 managed runtime with SnapStart), with the
same Spring Boot app that runs locally as a server. It left open how the app is attached to Lambda
and whether a cold start is fast enough. The tightest limit is Slack: an approval click must be
answered within 3 seconds, and the click handler records the decision in DynamoDB and updates the
Slack message (`chat.update`) before it answers. A click after hours of quiet always lands on a
cold function. The control plane also has scheduled work (re-enqueue, approval post and expiry
sweeps) that cannot run on a timer inside a function that is frozen between requests.

The app is Spring Boot 4.1 (Spring Framework 7, Jackson 3, Servlet 6.1). Options for the HTTP
part:

- **aws-serverless-java-container** (`aws-serverless-java-container-springboot4` 3.0.x): a
  handler class turns API Gateway events into servlet requests for the Spring MVC app, in process.
  No web server, no extra process.
- **AWS Lambda Web Adapter**: a Lambda extension that runs the unchanged jar with Tomcat and
  forwards events to it over loopback. No code change, but a second process, a layer to keep
  current, and Tomcat's own startup inside the snapshot.
- **Spring Cloud Function's AWS adapter**: built for function beans; HTTP support goes through the
  same serverless-web code as the first option, with more configuration.
- **A lighter framework** (ADR 0007's fallback): a rewrite, only if Spring cannot meet the limit.

## Decision

- **Adapter:** aws-serverless-java-container for Spring Boot 4. The entry is
  `dev.agentplatform.controlplane.lambda.LambdaHandler::handleRequest` (a `RequestStreamHandler`),
  for API Gateway HTTP API events (payload format 2.0). Spring starts in the handler's static
  initializer, so with SnapStart it starts once, before the snapshot.
- **The library's auto-configuration is excluded.** It brings `spring-cloud-function-serverless-web`,
  whose `ServerlessAutoConfiguration` replaces Tomcat in every run, local ones included (52
  integration tests broke). `application.yml` excludes it; on Lambda the handler registers its own
  server factory, so the app does not need it.
- **Sweeps:** the same function, invoked directly with `{"sweep": "all"}` by EventBridge Scheduler
  (every 5 minutes, M6). It runs `Sweeps.runOnce()` (re-enqueue, post unsent approvals, expire
  old ones) and answers `{"requeued": n, "posted": n, "expired": n}`. An HTTP event always carries
  `routeKey`, so no request through API Gateway can trigger a sweep; only an identity with
  `lambda:InvokeFunction` can. On Lambda the in-process `@Scheduled` sweep is always off
  (`platform.sweep.interval-seconds=0`); locally it stays.
- **SnapStart with priming:** the function is called through a published version or alias. Before
  the snapshot, a CRaC `beforeCheckpoint` hook sends four requests through the app that end before
  any store, queue or Slack call: a Slack `view_closed`, a GitHub `ping`, `POST /runs` with a bad
  body, and a request without an API key (answers 200, 200, 400, 401). They load the filters,
  controllers, Jackson, HMAC and problem-response classes into the snapshot without opening a
  connection a restore would find dead. `LAMBDA_PRIMING=off` skips them (for measuring).
- **Size:** 1769 MB (one full vCPU), arm64, Java 21. Memory is a Terraform variable, so it can
  change without code.
- **Package:** `./gradlew lambdaZip` builds a zip with classes at its root and the dependencies in
  `lib/` (about 40 MB, 110 jars), the layout the Java runtime loads fastest.

### Measurements (2026-11-19, us-east-1, arm64)

`control-plane/scripts/lambda_trial.py` deployed the zip next to a throwaway DynamoDB table and
sent signed Slack approval clicks straight to the function (`lambda invoke`, API Gateway events;
API Gateway itself adds about 10-30 ms). For each setting, 5 clicks went out at once right after a
version was published, so each landed on a freshly restored environment (5/5 every time), then 20
clicks in a row on warm environments. Slack calls went to slack.com with a made-up token: one real
round trip that fails with `invalid_auth` (the decision is kept). Medians, max in brackets; "total"
is the function's restore plus run time from its REPORT line; "client" is the laptop's clock
(includes about 100 ms of laptop-to-region network).

| Memory  | Priming | Restore    | First click run | Cold total       | Cold, client     | Warm run |
| ------- | ------- | ---------- | --------------- | ---------------- | ---------------- | -------- |
| 1024 MB | on      | 866        | 1766            | 2629 (2712)      | 3141 (3212)      | 46       |
| 1024 MB | off     | 862        | 1954            | 2816 (2923)      | 3026 (3114)      | 50       |
| 1536 MB | on      | 908        | 1196            | 2086 (2236)      | 2607 (2644)      | 37       |
| 1769 MB | on      | 905        | 1056            | 1961 (2134)      | 2242 (2420)      | 38       |
| 2048 MB | on      | 672        | 895             | 1590 (2147)      | 1814 (2371)      | 39       |
| 2048 MB | off     | 872        | 1035            | 1959 (1986)      | 2171 (2186)      | 38       |

(milliseconds). Priming saves about 150-190 ms of the first request. Restore varies by about
±200 ms between runs on its own. Each published version took about 85 s to be ready (snapshot).
A sweep answered in 45-60 ms. The rule for the size: the smallest whose slowest cold click stays at
or under about 2.5 s on the client clock, leaving room for API Gateway and Slack's own side of the
3 s. 1024 MB fails the limit; 1536 MB is over the margin; 1769 MB is the first that meets it.

## Consequences

- A cold approval click answers in about 2-2.5 s, inside Slack's 3 s, with no warm pings and no
  provisioned concurrency: still $0 while idle (ADR 0007). Memory costs nothing while idle, and
  per request it is a fraction of the free tier at this scale.
- What is left of a cold start is about 0.9 s of snapshot restore (AWS's side) and about 1 s of
  first connections (DynamoDB, slack.com, credentials) that priming cannot open ahead. If the
  margin turns out too thin on AWS (M7), the order is: 2048 MB (about 0.4 s faster, measured),
  then a scheduled warm ping, then provisioned concurrency (an hourly cost).
- Every deploy waits about 85 s for the snapshot. Only published versions use SnapStart: infra
  points the API at an alias, never `$LATEST`.
- State in the snapshot is shared by every environment restored from it. `java.security.SecureRandom`
  (run ids) is reseeded by the Java managed runtime after a restore; nothing else here needs to be
  unique per environment (`ThreadLocalRandom` only spreads retry waits). New code must not create
  ids, secrets or seeds in static or startup code with a non-CSPRNG.
- Startup stays free of network calls (already a convention); a new client that connects eagerly
  would put a dead connection into the snapshot.
- The library's auto-configuration exclusion must stay in `application.yml`; a library upgrade
  that renames it would break local runs and tests, not Lambda, so the tests catch it.
- Re-measuring is one command (`lambda_trial.py --configs <memory>:<on|off>,...`); the script
  makes its own throwaway resources and deletes them.
