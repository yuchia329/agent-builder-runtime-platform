# 0009. The worker owns the agent loop

- Status: Accepted
- Date: 2026-11-16
- Supersedes: the "no custom port for the model" part of [ADR 0004](0004-ports-and-adapters.md)

## Context

A run is a loop: send the conversation to the model, act on the tool calls it asks for, send the
results back, until it answers or a limit is hit. The platform's main value lives between those
steps: budget checks, refusing tools the spec does not allow, checking tool arguments, pausing for
approval, and (from M2) a checkpoint and an idempotency check per step.

The first design ran the loop with the Strands Agents SDK and attached platform rules through
Strands hooks. Whether those hooks can stop a run mid-turn for an approval, or checkpoint every
step and resume exactly there, was an open question. Running the loop inside Strands would also
put most worker logic outside `worker/core/`, which may not import vendor SDKs (ADR 0004).

Options considered:

- **Strands runs the loop; platform rules in hooks.** Less code, more of Strands exercised (useful
  for M8, fixing Strands bugs upstream). Crash safety and approvals depend on hook behavior we do
  not control.
- **Our own loop in `worker/core/`; Strands (or the Bedrock API) only behind a model port.** About
  100 lines we own. Every model reply passes through code we can test without a network.

## Decision

- `worker/core/handle_run.py` runs the agent loop. It talks to the model only through a `Model`
  port: conversation and tool specs in; text or tool calls, a stop reason and token usage out.
- Model adapters translate vendor formats and retry transient vendor errors themselves. The stub
  adapter plays back scripted turns. The Bedrock adapter (M1) may use a Strands model provider or
  the Bedrock Converse API directly; that choice stays inside the adapter.
- Platform rules are plain code in the loop: budget before every step, tool allowlist, argument
  validation against the tool schema, approval pause, refused or cut-off replies treated as
  failures, any crash ends the run `FAILED`.

## Consequences

- Budgets, approvals, and later checkpoints and idempotency are ordinary code with unit tests; no
  dependence on a framework's hook timing.
- `worker/core/` stays free of vendor SDKs; a test enforces it.
- We own conversation handling: retries policy, context trimming as conversations grow, and the
  message format. Retries and trimming are added in M1 with the real model.
- Less of Strands is exercised, so M8 (Strands fixes upstream) has a smaller surface; it now rests
  on the model provider layer.
