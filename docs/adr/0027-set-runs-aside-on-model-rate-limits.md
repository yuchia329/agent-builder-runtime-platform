# 0027. Set a run aside when the model keeps answering 429

- Status: Proposed
- Date: 2026-11-21

## Context

The model adapters retry rate limits (429) and server errors 4 times with backoff and honor
`Retry-After` up to 30 s. When the retries run out, the adapter raises `ModelError` and the run
fails with reason `error`. Under load, a provider's rate limit lasts longer than those retries:
every run that hits it fails, and nothing tells a rate limit apart from a real error. Holding the
worker thread and waiting longer keeps a slot and a lease busy doing nothing.

Options looked at: retry longer inside the worker (holds the slot, the lease and a thread; the
time counts as working time); keep the queue message and let it reappear after its visibility
timeout (counts toward the dead-letter queue's 5 receives, and the run stays RUNNING, holding its
tenant's slot); set the run aside: back to QUEUED, re-queued with a delay.

## Decision

- After the adapter's retries, a rate limit is its own error (not `error`), and the worker calls
  `POST /internal/runs/{run_id}/defer` with `reason: rate_limited` and a delay (the provider's
  `Retry-After` if known, else a backoff, at most 900 s).
- The control plane moves the run from RUNNING to QUEUED, frees its slot (ADR 0026) and re-sends
  its message with that delay. The worker deletes its message and releases the lease. The run
  resumes from its checkpoints like any redelivery.
- After 15 minutes set aside in total, the control plane fails the run with `rate_limited`
  instead of re-queuing it.
- Time set aside is not working time, so it does not count against `budget.max_seconds`
  (ADR 0012).

## Consequences

- A provider's rate limit slows runs down instead of failing them, and its share is visible:
  runs set aside and `rate_limited` failures can be counted.
- One new internal route and a RUNNING → QUEUED change made only by the control plane; the shapes
  are in `contracts/internal-api.openapi.yaml`.
- The 15-minute total is a fixed rule for now; a per-spec setting can come later.
- Tool rate limits (the gateway's connectors) are unchanged: the model still sees a tool error
  and decides.
