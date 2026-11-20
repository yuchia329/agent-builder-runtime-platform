# 0023. Route throttling at the API, a model spend alarm on a metric the worker writes

- Status: Proposed
- Date: 2026-11-20

## Context

The API on its default `execute-api` address (ADR 0021) is public, and model providers bill per
token outside AWS (MiniMax), so neither AWS Budgets nor the project's $10 budget sees that spend.
ADR 0007 named the protections: route throttling, per-run budgets, and alerts. Per-run budgets
exist (spec `budget`, worker); this decision covers the other two.

**Throttling.** HTTP APIs have no usage plans or per-client limits; a stage sets a default per
route (each route its own token bucket), and routes can override it. The account allows 10
concurrent Lambda executions in this region (dev), shared by both functions; above that Lambda
answers with errors (503 at the API), not 429. Measured on dev (2026-11-20, `throttle_check.py`,
rate 5, burst 10):

- 30 requests at once to `GET /agents`: no 429; 23 answered 401 (admitted), 7 answered 503
  (Lambda's concurrency limit).
- 40 and 100 unsigned requests at once to an IAM route (`/internal/*`): all 403. Requests refused
  by IAM authorization are not counted against the limit.
- A steady stream, 8 at a time, 80 requests in 3.2 s (25 per second): 6 answered 429, 74 were
  admitted. AWS documents throttling as best-effort targets; each API Gateway host keeps its own
  count, so the admitted rate was well above 5 per second.

**Spend metric.** The worker knows each model call's cost (`core/models.py` prices) and records it
on the "model.call" span (`cost.usd`) and in the run's `cost_usd`. Options looked at:

- A CloudWatch Logs metric filter on the spans in `aws/spans`: infra only, but metric filter
  patterns cannot select a key with dots (`attributes."cost.usd"`; checked with
  `TestMetricFilter`: `$.attributes.cost.usd` matches nothing, bracket forms are invalid), and it
  would tie the alarm to tracing being on.
- The control plane publishes the run's cost when a run ends: misses runs that never end (a run
  that keeps crashing spends on every attempt) and adds work to the Lambda path.
- The worker writes the cost of every model call as a CloudWatch embedded metric format (EMF) log
  line; on Fargate the `awslogs` driver ships it and CloudWatch extracts the metric. No API call,
  no SDK, counts spend as it happens.

## Decision

- **Throttling:** the API stage sets a default for every route, `api_throttle_rate` 5 requests
  per second and `api_throttle_burst` 10, under the account's Lambda concurrency so a burst gets
  429 before Lambda limits it. It is a soft limit: the hard ceiling remains Lambda's 10 concurrent
  executions. No reserved concurrency (the account has none left to reserve).
- **Spend metric:** the worker writes one EMF line per model call: namespace from
  `METRICS_NAMESPACE` (`AgentPlatform/dev`, set in the task definition), metric `ModelCostUsd`
  (US dollars, no dimensions), alongside its normal logs. No dimensions keeps it one metric (each
  dimension combination is billed as its own metric). Unset `METRICS_NAMESPACE` writes nothing
  (local runs, tests).
- **Alarm:** `agent-platform-dev-model-spend` sums `ModelCostUsd` over 86400 s and emails through
  SNS topic `agent-platform-dev-alerts` (address `alert_email`, git-ignored) above
  `model_spend_alarm_usd` (1 USD), on ALARM and on OK; missing data is not breaching. A one-day
  period is a window ending at each evaluation, so it means the last 24 hours, not a calendar
  day: a test value of 2 USD fired the alarm within about 70 s and keeps it in ALARM for 24 hours.
- **Log retention:** every log group the platform writes keeps 14 days, including those an AWS
  feature made (`aws/spans`, `/aws/application-signals/data`), taken over with `import` and
  `skip_destroy`.

## Consequences

- A flood of public requests gets some 429s, but a sustained flood still admits far more than
  5 per second until Lambda's limit; protection against cost rests on Lambda's concurrency limit
  and on API keys and signatures checked in the app. Throttling is per API, not per caller: a
  flood also delays Slack clicks and GitHub webhooks (GitHub does not retry a 429; redeliver from
  the App's settings).
- Raising Lambda concurrency (a free quota request) means raising the throttle with it, or the
  limit stops protecting anything.
- The alarm sees only providers whose calls go through the worker with a price in
  `core/models.py`; a model priced 0 there spends unseen. Bedrock spend also shows in AWS's bill
  and budgets.
- An alarm is an email, not a stop: runs keep going after it fires. Per-run budgets cap each run;
  stopping all runs is a manual step (set the worker service to 0, or `worker_max_tasks = 0`).
- The worker owns the metric line; infra owns the namespace (task definition) and the alarm. The
  name `ModelCostUsd` is shared between them, recorded in `docs/architecture.md` → Observability.
- Cost: the alarm is within the free 10; the custom metric is $0.30 a month in months with data;
  email notifications are free at this volume.
