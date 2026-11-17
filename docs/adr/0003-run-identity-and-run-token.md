# 0003. Run identity via `acts_as` and a signed run token

- Status: Accepted
- Date: 2026-11-16

## Context

The tool gateway must pick a credential for each call. The first plan said "the calling user's
token", but a webhook-triggered run has no calling user. And if the worker simply tells the gateway
which user to act as, a compromised worker could claim any identity, which defeats keeping tokens
out of the worker.

## Decision

- Each agent spec declares `acts_as`: `owner` (the agent's publisher) or `team-bot` (a GitHub App
  installation or Slack bot for the tenant).
- When a run starts, the control plane issues a short-lived JWT with `run_id`, `tenant`,
  `agent_version`, `acts_as` and the tools the spec allows, expiring after the run's `max_seconds`.
- The worker attaches it to every gateway call. The gateway verifies the signature, picks the
  credential from `acts_as`, and refuses tools not listed.
- For `require_approval` tools the gateway also confirms with the control plane that an approval
  exists.

## Consequences

- A compromised worker can only do what that one run was allowed to do, for that run's lifetime.
- Actions by team bots show as the bot, and keep working when a person leaves the team.
- Needs a signing key pair (control plane signs, gateway verifies) and key rotation.
- Claims shape lives in `contracts/run-token.schema.json`.
