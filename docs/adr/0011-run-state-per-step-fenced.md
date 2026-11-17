# 0011. Run state: one item per step, fenced writes, 15-day retention

- Status: Accepted
- Date: 2026-11-16

## Context

M2 makes runs survive a worker crash: a lease so one worker runs a run at a time, checkpoints so
another worker resumes where the first stopped, and idempotency keys so no tool call runs twice.
The architecture names these and the run-state DynamoDB table, but not how a run's conversation
is stored, what stops a worker that lost its lease from writing, or how long the data stays.

Options considered for checkpoints:

- **One item holding the whole conversation, overwritten each step.** Simple to read, but the item
  grows with the run. DynamoDB items stop at 400 KB; a long run on a large-context model can pass
  1 MB.
- **One item per step (what the step added), appended.** Each item is small and bounded: a model
  turn is at most `max_tokens`, tool results are kept short by the tool adapters. Resuming reads
  all step items of the run in one query and replays them. This is the common pattern (Strands'
  S3 session manager stores one object per message; LangGraph and the OpenAI Agents SDK store
  per step or per message rows).
- **Conversation in S3, pointer in DynamoDB.** Needed only for large payloads; a second service
  and a costlier write (an S3 PUT costs more than a small DynamoDB write) for no gain at our sizes.

Lease alone does not stop a stalled worker: if worker A pauses past its lease expiry and worker B
takes over, A can wake up and write on top of B's work, which can repeat a tool call.

## Decision

- The run-state table has one partition per run: `LEASE` (owner, expiry, `last_step`),
  `STEP#000001`... (one item per step, never changed), `TOOL#<key>` (idempotency claim and result).
- **Fencing:** every write except taking the lease is a DynamoDB transaction whose first part
  checks the writer's live lease. A checkpoint also moves `last_step` from n-1 to n in that check,
  so "step n only after n-1" is atomic. A worker without the lease gets `LeaseLost` and changes
  nothing.
- A step's payload is at most 350 KB of JSON, else `StateError` (the run fails clearly). If a tool
  ever needs more, store that payload in S3 with a pointer (new ADR).
- **Retention:** every item carries `ttl` = last write + 15 days; DynamoDB TTL deletes expired
  items in the background at no cost. Run state is crash-recovery scratch space, not history.

## Consequences

- Writes cost about twice a plain put (transactions), still a small fraction of a run's model
  cost; storage is around 40 KB per triage run and, with TTL, bounded (about 600 MB at 1,000 runs
  a day).
- A run must finish within 15 days of its last write, or its state is gone and it would restart
  from step 1. Approval waits (M3) need a timeout below that.
- Long-term conversation history (audits, eval datasets) needs its own store, for example a copy
  of finished runs to S3; it is not the run-state table's job.
- The in-memory adapter enforces the same rules, checked by one contract suite.
