# 0013. A tool call with an unknown outcome is repeated only if the tool is safe to repeat

- Status: Accepted
- Date: 2026-11-17

## Context

Every tool call claims an idempotency key before it runs and stores its result after
(ADR 0011). A resumed run reuses a stored result. One case stays open: the key is claimed but no
result is stored, because the worker died while the tool ran or just after. The action may or may
not have happened, and the worker cannot tell.

Options considered:

- **Always repeat the call.** Simple and self-healing, but a Slack post or a comment can happen
  twice, which breaks M2's promise of no duplicate tool calls.
- **Tell the model the outcome is unknown and let it decide.** The run can recover on its own,
  but the model may simply call the tool again.
- **Decide per tool.** Repeat tools whose repeat is harmless; for the rest, stop and let a
  person check.

## Decision

- Tools declare `read_only` (changes nothing) and `idempotent` (twice has the same effect as
  once), named after MCP's tool annotations (`readOnlyHint`, `idempotentHint`), so the M4
  gateway can pass an MCP server's own annotations through.
- A claimed call without a stored result is repeated if the tool is read-only or idempotent.
  Otherwise the run fails with reason `error` and a message saying the outcome is unknown and the
  call was not repeated.
- A tool without flags is treated as unsafe. Stored results include tool errors, so a failed call
  is not retried on resume either.

## Consequences

- No tool that is unsafe to repeat runs twice because of a crash; the price is a failed run that
  a person checks and reruns, only when a worker dies during such a call.
- Every new tool must declare its flags truthfully; a wrong `idempotent` brings duplicates back.
