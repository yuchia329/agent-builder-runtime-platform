# 0004. Swappable backends behind ports with contract tests

- Status: Accepted; the control-plane database exception is superseded by [0008](0008-control-plane-data-in-dynamodb.md), the model exception by [0009](0009-worker-owns-agent-loop.md)
- Date: 2026-11-16

## Context

The platform depends on SQS, DynamoDB, Bedrock, Slack and Secrets Manager. We want to run without
AWS in tests, and to be able to swap a backend (for example Redis instead of SQS) without changing
the core logic, while keeping the same behavior.

## Decision

- Each swappable backend sits behind an interface (port) defined by what the platform needs, not
  by the vendor's API: `RunQueuePublisher`, `RunQueue`, `RunStateStore`, `ToolClient`,
  `ApprovalChannel`, `TokenStore`. Config selects the adapter.
- Every port has one contract test suite that runs against every adapter. An adapter that passes
  may be swapped in.
- Start with two adapters per port: the AWS one and an in-memory one.
- No custom port for the control-plane database (JPA and Flyway already abstract it) or for the
  model (Strands already supports several providers).

## Consequences

- Core logic and most tests run without AWS or Docker.
- "Same behavior" is enforced by tests, not by convention.
- Ports must not be weakened to fit a weaker backend: one without atomic conditional writes cannot
  back `RunStateStore`.
- More files per service, and each new adapter must pass the full contract suite.
