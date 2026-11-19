# 0016. Agent evals run the worker's code in process, against simulated services

- Status: Proposed
- Date: 2026-11-18

## Context

M5 adds evals: for any spec, a set of tasks (a trigger event, a starting state of the outside
world, checks) that score the agent's decisions when its prompt, model or tools change. What
decides the agent's behavior is the spec, the model, the tool descriptions and errors it reads,
and the worker's loop (trimming, safeguards). The queue, the tables, the control plane and where
the code runs do not change a decision; platform correctness has its own tests (`make demo`,
`make chaos`), and performance is measured on AWS in M7.

Evals must never touch real outside APIs, must run on a developer's machine, and must not need a
second copy of the model-provider code. The boundaries say only the worker calls model providers
(ADR 0010) and only the gateway calls outside APIs (ADR 0006).

Options:

- **Full local stack per task** (`docker compose`, publish the spec, start runs through the API).
  Closest to production, but slow to start, needs Docker and the Java build, and the extra parts
  add nothing to what is measured.
- **A copy of the agent loop in evals**, with tools faked inside it. Fast, but it measures a
  different loop and different tool descriptions than the agent really gets.
- **The worker's code in process, the real gateway as a subprocess pointed at simulators.**

## Decision

The `evals/` component imports the `worker` package (a path dependency) and runs `handle_run`
with in-memory adapters (control plane, run state under a lease) and the model the spec names,
built by the worker's own config and wiring. Tools go through `GatewayTools` to the real tool
gateway, started as a subprocess with the spec's connectors pointed at simulators on loopback
(`CONNECTOR_<NAME>_BASE_URL`), throwaway credentials and run-token keys, and an internal run
endpoint for its approval check. Every approval request is approved; checks decide whether it
should have been asked.

Simulators model the service's state (GitHub: issues and labels, search by words, labeling and
closing change it), not recorded request-response pairs, because a model's arguments differ from
run to run.

Model calls from evals go through the worker's adapters, so the rule "only the worker calls model
providers" holds for the code: evals adds no provider layer. The LLM judge (M5) uses the same.

## Consequences

- Evals measure the code that runs in production: the same loop, connector rules, hooks, tool
  descriptions and error messages. A gateway or worker change shows up in evals without a copy to
  update.
- No Docker; a task starts in milliseconds once the gateway is up, which makes repeats cheap.
- `evals/` depends on the worker's internals (`handle_run`, `Deps`, `InMemoryControlPlane`,
  `Config`, `wiring.build`): a change there can break evals, so a worker change should also run
  the evals tests.
- Each connector needs a simulator to be evaluated realistically; until then scripted responses.
  Simulators must follow the real API's shapes (copied from cleaned recordings) and are a model of
  it, not a copy (GitHub search has no stemming here).
- What evals cannot see: queueing, leases across workers, crash recovery, latency. Those stay with
  the platform tests and M7.
