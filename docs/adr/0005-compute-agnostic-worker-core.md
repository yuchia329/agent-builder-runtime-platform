# 0005. Compute-agnostic worker core

- Status: Accepted
- Date: 2026-11-16

## Context

Workers run on ECS Fargate as a pool. Short runs could also suit AWS Lambda, which handles SQS
polling, redelivery and the DLQ for us, but bills for time spent waiting on the model and caps runs
at 15 minutes. We do not want to choose once and be stuck.

## Decision

The worker's logic is one function, `handle_run(run_id)`, with no knowledge of how it was invoked.
Thin entrypoints call it: `poll_loop.py` for Fargate (long-poll, heartbeat, concurrency) now, and
`lambda_handler.py` for Lambda later if needed.

## Consequences

- Fargate ships first; Lambda becomes a deploy option, not a rewrite.
- Lease, checkpoint and idempotency logic must not assume a long-lived process.
- Choosing compute per agent (for example a `runtime:` spec field) is possible later but not
  planned.
