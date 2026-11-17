# Architecture Decision Records

One file per significant design decision: the context, the choice, and its consequences.

Rules:

- Never rewrite an accepted ADR. To change a decision, add a new ADR and mark the old one
  `Superseded by NNNN`.
- [architecture.md](../architecture.md) describes the current design and links here for the why.
- Write an ADR when a choice is hard to reverse, surprising, or had a real alternative.

## Index

| #                                                   | Decision                                             | Status   |
| --------------------------------------------------- | ---------------------------------------------------- | -------- |
| [0001](0001-workers-use-control-plane-api.md)       | Workers reach run data through the control plane API | Accepted |
| [0002](0002-control-plane-owns-approvals.md)        | The control plane sends and receives approvals       | Accepted |
| [0003](0003-run-identity-and-run-token.md)          | Run identity via `acts_as` and a signed run token    | Accepted |
| [0004](0004-ports-and-adapters.md)                  | Swappable backends behind ports with contract tests  | Accepted, partly superseded by 0008, 0009 |
| [0005](0005-compute-agnostic-worker-core.md)        | Compute-agnostic worker core                         | Accepted |
| [0006](0006-separate-tool-gateway.md)               | Tool gateway as a separate service, built in stages  | Accepted |
| [0007](0007-serverless-scale-to-zero-deployment.md) | Serverless, scale-to-zero deployment                 | Accepted |
| [0008](0008-control-plane-data-in-dynamodb.md)      | Control-plane data in DynamoDB                       | Accepted |
| [0009](0009-worker-owns-agent-loop.md)              | The worker owns the agent loop                       | Accepted, Bedrock-only adapter plan superseded by 0010 |
| [0010](0010-provider-neutral-model-layer.md)        | Provider-neutral model layer                         | Accepted |
| [0011](0011-run-state-per-step-fenced.md)          | Run state: one item per step, fenced writes, 15-day TTL | Accepted |
| [0012](0012-time-budget-counts-working-time.md)    | The time budget counts working time across attempts | Accepted |
| [0013](0013-unknown-tool-outcome.md)              | Unknown tool outcome: repeat only safe-to-repeat tools | Accepted |

## Template

```markdown
# NNNN. Title

- Status: Proposed | Accepted | Superseded by NNNN
- Date: YYYY-MM-DD

## Context

What problem or force makes a decision necessary.

## Decision

What we will do.

## Consequences

What gets easier, what gets harder, what we now have to maintain.
```
