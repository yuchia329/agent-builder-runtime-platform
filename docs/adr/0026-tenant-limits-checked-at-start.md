# 0026. Tenant limits checked at start, fairness from an SQS fair queue

- Status: Proposed
- Date: 2026-11-21

## Context

The platform runs every tenant's runs on one worker pool from one SQS queue. M7 showed one
tenant can fill it: 1,000 runs queued in 50 s, 918 executing at once on 20 workers. With a second
tenant, its runs would wait behind that backlog, and one tenant's spending had no limit beyond
each run's own budget (ADR 0012) and an account-wide alarm that only sends email (ADR 0023).
Today there is one tenant (one API key in the control plane's settings).

Options looked at for fairness and a cap on runs at once:

- A queue per tenant: workers poll many queues and need a policy across them; a new tenant needs
  infrastructure.
- Hold runs before enqueue (the control plane enqueues only while the tenant is under its cap,
  a sweep releases the rest): the control plane would track every waiting run, and releases wait
  for the sweep.
- Admission at start: the worker's QUEUED → RUNNING update takes a slot; at the cap the run is
  re-queued with a delay. One check on a path every run already takes.
- SQS fair queues: on a standard queue, messages with a `MessageGroupId` are delivered so that a
  group with a backlog does not delay the others' messages. Fairness without new queues.

## Decision

- **Tenants** get a record with their limits: `max_running` (runs executing at once) and
  `daily_usd` (spend per UTC day). API keys are per tenant and stored hashed.
- **Fair queue:** the control plane sends every run message with `MessageGroupId` = tenant id.
- **Cap at start:** QUEUED → RUNNING takes a slot with a DynamoDB conditional update on the
  tenant's running counter (`running < max_running`). At the cap the control plane re-sends the
  run's message with `DelaySeconds` and answers 429 (`Deferred`, `Retry-After`); the run stays
  QUEUED and the worker deletes its message. The control plane re-sends because it is the only
  sender to the queue, and a new message starts a new receive count (5 receives move a message to
  the dead-letter queue). A RUNNING → RUNNING resume after a crash takes no new slot.
- **Slots are freed** when a run ends, waits for an approval (up to 72 hours) or is set aside
  (ADR 0027). A sweep recounts each tenant's RUNNING runs and corrects the counter if a slot
  leaked.
- **Daily spend cap:** the control plane adds each cost update to a counter per tenant per day.
  Over the cap, `POST /runs` answers 429 (`tenant_budget`) and a queued run fails `tenant_budget`
  when it starts (409 to the worker). Runs already executing finish within their own budgets, so
  a day can end slightly over the cap.

## Consequences

- Shapes in `contracts/internal-api.openapi.yaml` (429 at start, `DeferredProblem`, failure
  reasons) and the run message's description; the control plane and the worker follow them.
- A tenant at its cap costs one extra receive and one re-send per waiting run every delay; the
  delay (up to 900 s, SQS's limit) trades queue traffic against start latency.
- Fairness is SQS's: it reduces the wait of a quiet tenant behind a noisy one; it does not
  reserve capacity. The cap is what bounds a tenant's share of the workers.
- The counters live in the control-plane table, so only the control plane reads or writes them
  (ADR 0001, ADR 0008).
- Mapping GitHub App installations or Slack workspaces to tenants is not part of this; webhooks
  keep starting runs for the first tenant.
