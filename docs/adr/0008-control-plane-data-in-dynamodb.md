# 0008. Control-plane data in DynamoDB

- Status: Accepted
- Date: 2026-11-16
- Supersedes: the "no custom port for the control-plane database" part of ADR 0004

## Context

ADR 0007 moves the control plane to Lambda so the platform costs nothing when idle. The control
plane stores tenants, agents, spec versions, runs and approvals. The first plan used PostgreSQL.

Options considered:

- **RDS PostgreSQL.** Billed per hour even when idle; stopped instances restart after 7 days. Lambda
  connection churn usually calls for RDS Proxy, which also bills per hour.
- **Aurora Serverless v2 with auto-pause.** No compute cost while paused, but resuming takes about
  15 seconds, longer than Slack's 3-second callback limit and close to GitHub's webhook timeout.
  It also puts the Lambda in a VPC.
- **SQLite on an always-on EC2 instance.** Keeps a server running around the clock, and SQLite is a
  file, not a network database, so Lambda would need yet another service in front of it.
- **PostgreSQL on an existing EC2 instance, Lambda in the same VPC.** The database stays private
  (its security group admits only the Lambda's security group). But a VPC-attached Lambda has no
  outbound internet, even in a public subnet, and the control plane must reach SQS and Slack. That
  needs a NAT gateway (about $33 a month) or the EC2 acting as a NAT instance, which makes one
  box the database, the NAT and a single point of failure, with backups and upgrades done by hand.
  Opening the database port to Lambda without a VPC does not work either: Lambda outside a VPC has
  no dedicated IP range to allow, only AWS's shared regional ranges.
- **DynamoDB on-demand.** Billed per request plus storage, nothing per hour, no connections, no VPC,
  single-digit-millisecond reads. Already used by the worker, so the team and local stack know it.

## Decision

- The control plane stores its data in its own DynamoDB table (on-demand capacity), separate from
  the worker's run-state table. Only the control plane reads or writes it, so ADR 0001 still holds:
  workers reach run data only through the internal API.
- Data is modeled around access patterns, not tables: tenant by API key, agent by id, agents by
  trigger, versions of an agent, run by id, runs of an agent by time, approvals of a run.
- Rules that SQL constraints would have enforced become conditional writes:
  - A version number is allocated by an atomic counter on the agent item, and the version item is
    written only if it does not exist (versions are immutable).
  - Every run status change is a conditional update on the expected current status, so the run
    state machine cannot skip or repeat a transition.
- Control-plane persistence sits behind ports (`AgentStore`, `RunStore`) with a DynamoDB adapter and
  an in-memory adapter, and one contract suite per port, as ADR 0004 requires for other backends.
  Spring Data JPA and Flyway are not used.

## Consequences

- No hourly database cost; storage stays within the DynamoDB free tier at demo scale.
- No ad-hoc SQL queries or joins. New queries need a new key design or a secondary index, planned
  up front.
- No schema migrations. Item shapes are versioned in code and must stay backward compatible with
  items already stored.
- Writing a status change and enqueuing to SQS are two steps with no shared transaction (as with
  PostgreSQL before). A run left `QUEUED` with no message is re-enqueued by a periodic sweep; the
  worker lease makes duplicates harmless.
- Item size is capped at 400 KB. Spec JSON fits; run steps are stored as separate items under the
  run, not inside the run item.
- Locally the control plane uses DynamoDB Local. PostgreSQL leaves the local stack.
