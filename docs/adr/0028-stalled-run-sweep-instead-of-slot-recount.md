# 0028. A stalled-run sweep frees leaked slots, not a counter recount

- Status: Proposed
- Date: 2026-11-21

## Context

ADR 0026 gives each tenant a counter of slots (runs executing at once) and planned a sweep that
recounts each tenant's RUNNING runs and corrects the counter "if a slot leaked". While building
it, the control plane came to change the counter only in the same DynamoDB transaction as the
run's status change (taking a slot on QUEUED → RUNNING, freeing it on any move out of RUNNING),
with a `holdsSlot` mark on the run so a run started before M8 never frees a slot it did not take.
With that, the counter cannot drift from the runs that hold a slot, so a recount would never
change it. A recount would also need a list of tenants and a guard against starts and ends that
happen while it counts.

The leak that does remain is a run stuck in RUNNING: its worker crashed often enough that the
queue moved its message to the dead-letter queue (5 receives), so nothing will ever end the run.
It holds a slot for good, and enough of them stop the tenant at its cap. A recount counts such a
run as running, so it would not help.

## Decision

- Slots change only in the transaction that changes the run's status; there is no recount.
- A sweep in the existing sweep pass fails runs that have been RUNNING with no update for
  `SWEEP_RUNNING_STALLED_AFTER_SECONDS` (default 5,400 s, 90 minutes) with reason `error`
  ("No progress for 90 minutes: the run's worker is gone"), which frees their slot. A run's
  working time is at most 3,600 s (`budget.max_seconds`), and approval waits and set-asides are not
  RUNNING, so a live run reports well within that time.
- RUNNING runs are listed by a sparse index entry (`GSI1PK = RUNS#RUNNING#<0-7>`, sort key the
  last update time), spread over 8 partitions because every progress report rewrites it. The
  sweep fails a run only if its last update is still the one it listed.

## Consequences

- A tenant can lose slots for at most 90 minutes plus one sweep interval (5 minutes on Lambda).
- A worker that is somehow still working on a swept run gets 409 on its next report and stops.
- Each progress report also writes the index entry: one more index write per update.
- ADR 0026's "a sweep recounts each tenant's RUNNING runs" is replaced by this; the rest of ADR
  0026 stands.
