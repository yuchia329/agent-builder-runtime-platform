# 0012. The time budget counts working time across attempts

- Status: Accepted
- Date: 2026-11-16

## Context

A spec's `budget.max_seconds` limits how long a run may take. With checkpoints (ADR 0011) a run
can be worked on in several attempts: a worker crashes or loses its lease, the message waits for
its visibility timeout (60 s or more), and another worker resumes. The budget has to mean
something across those attempts.

Options considered:

- **Wall-clock time since the run first started.** Easy to explain ("no run takes longer than
  3 minutes end to end"), but the platform's own failures use it up: one crash plus the
  redelivery wait can consume most of a 180 s budget, and the resumed run then fails at once.
- **Working time, summed over attempts.** Each checkpoint stores the working seconds so far; a
  resumed attempt starts from that total. Time between attempts (crash, queue wait) is not
  counted.

## Decision

`max_seconds` counts working time: the seconds workers spend on the run, added up across
attempts. Each checkpoint stores the total as `elapsed_s`; `BudgetTracker` starts from it.

## Consequences

- A crash cannot exhaust a run's time budget; the budget limits how much worker time (and so
  model and tool activity) a run can use.
- End-to-end latency is not bounded by the budget. Waiting is bounded elsewhere: the queue's
  redelivery and DLQ limits, and (M3) approval timeouts.
- Work done after the last checkpoint of a failed attempt is not counted; at most one step.
