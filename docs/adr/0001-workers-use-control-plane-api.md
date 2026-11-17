# 0001. Workers reach run data through the control plane API

- Status: Accepted
- Date: 2026-11-16

## Context

Workers need the spec version for a run and must report status, steps and cost. Both live in
PostgreSQL, which the control plane owns. Workers could read and write those tables directly, or
go through the control plane.

## Decision

Workers never connect to PostgreSQL. The control plane exposes an internal API:
`GET /internal/runs/{id}` returns the run, its spec version and a signed run token;
`PATCH /internal/runs/{id}` records status, steps, cost, trace id and any pending approval call.
The shape lives in `contracts/internal-api.openapi.yaml`.

## Consequences

- One service owns the run state machine and the schema; migrations never break workers.
- Workers need no database credentials, so their IAM role and config stay small.
- Every status change is one extra HTTP hop. Acceptable at this scale.
- The control plane must be up for workers to start or finish a run.
