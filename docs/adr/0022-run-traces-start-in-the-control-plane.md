# 0022. Run traces start in the control plane; Lambda uploads no spans

- Status: Proposed
- Date: 2026-11-19

## Context

M6 adds OpenTelemetry tracing: one trace per run, across control plane, worker and tool gateway,
viewed in X-Ray. Three questions came up while building the control plane's part.

**Where a run's trace starts and how it travels.** A run is created by a request (`POST /runs`)
or a webhook (one delivery can start several runs), waits on the queue, may run several worker
attempts, and can pause up to 72 h for an approval. The run message body is a contract the
worker checks strictly (unknown fields are rejected), and the worker already reported a
`trace_id` of its own in `PATCH /internal/runs/{id}`.

**How spans reach X-Ray.** Options looked at:

- The ADOT collector as a Lambda extension, with classic X-Ray: AWS now lists the collector
  layers as "not recommended" and no longer documents them; the extension adds to every cold start.
- Transaction Search (account setting; spans stored in the `aws/spans` log group) with spans sent
  over UDP to the X-Ray agent Lambda runs when active tracing is on, using AWS's
  `aws-distro-opentelemetry-xray-udp-span-exporter`. Chosen first, then measured (below).
- Transaction Search with spans sent over HTTPS to the X-Ray OTLP endpoint (SigV4), flushed before
  each response.

**Measurements** (control plane, 1769 MB arm64, SnapStart with priming; cold Slack click, 5 cold
environments; `scripts/lambda_trial.py`):

| Setup | Restore + duration, median (max) |
| --- | --- |
| Before tracing (ADR 0017) | 1.96 s (2.13 s) |
| Tracing code, nothing uploaded | 2.00 s (2.20 s) |
| Tracing code, active tracing, spans over UDP | 2.29 s (2.54 s) |

With active tracing and UDP export, only about half of the requests' spans reached `aws/spans`
(matching Lambda's sampling of about 1 request per second plus 5%, which cannot be changed for
Lambda), and none from SnapStart-restored environments. Active tracing alone added about 250 ms
to the restore.

## Decision

- **The control plane starts each run's trace** when it creates the run: a "run.start" span with
  no parent (the request that caused it is linked, not the parent, so each run of a webhook gets
  its own trace). Its W3C `traceparent` is stored on the run (`traceParent`, with `traceId`) and
  sent as the SQS message attribute `traceparent`, not in the body, so workers that do not know it
  still accept the message. An approval decision ("run.approval") and a sweep's re-send
  ("run.requeue") are children of "run.start" and send their own `traceparent`. `trace_id` on the
  run is the control plane's; a worker's value is kept only for runs created before tracing.
- **Workers and the gateway send `traceparent`** on internal API and `/mcp` calls. Only
  `/internal/*` requests may continue a caller's trace; public routes always start their own.
- **The control plane makes spans but does not upload them on Lambda** (`TRACING_EXPORTER=none`,
  active tracing off). The OpenTelemetry SDK is set up by hand (no Java agent), ids come from
  `SecureRandom` (the default generator would repeat ids across SnapStart restores), spans cover
  each HTTP request and AWS SDK call, and `logging` prints them locally. Its cost on a cold click
  is about 40 ms.
- **Spans that matter are uploaded by the worker** (model calls, tool calls, checkpoints; Fargate,
  not on Slack's 3-second path), directly to the X-Ray OTLP endpoint with SigV4, which needs
  Transaction Search. No collector container.
- Spans carry tenant, agent, version and run ids; never input, output, tokens, keys or headers.

## Consequences

- A run's trace in X-Ray holds the worker's (and later the gateway's) spans under the trace id the
  control plane chose; the control plane's own steps are not in it. Control-plane latency is
  read from Lambda's logs (REPORT lines) instead.
- The contracts change in description only (run-message attribute, `traceparent` header,
  `trace_id` ownership); no schema version bump.
- Uploading control-plane spans later means an exporter that does not slow the response: HTTPS
  export before returning (estimated 50-300 ms per response, risky for Slack's 3 s) or a collector
  extension that exports after the response (adds to cold start). Either needs a new trial.
- Transaction Search is an account-wide setting (us-east-1); `aws/spans` needs a retention policy.
