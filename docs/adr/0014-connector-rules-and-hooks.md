# 0014. Connectors: declarative rules plus named hooks

- Status: Accepted
- Date: 2026-11-18

## Context

ADR 0006 says adding a connector is data (`openapi.yaml`, `allowlist.yaml`), not code: the
gateway generates one MCP tool per allowed operation. An OpenAPI description says what an API can
do, not what an agent may do. The M1 GitHub tools (`worker/.../tools_local.py`) enforce limits the
gateway must keep when it takes over in M4:

- only allowed repositories; search kept inside them (`repo:` added, `repo:` / `org:` / `user:`
  in the query refused, since GitHub search otherwise covers every repository the bot can see);
- results cut to a few fields, without issue bodies (untrusted text, and tokens cost money);
- close reasons limited to an enum;
- only labels that already exist (GitHub's add-labels call silently creates a misspelled label);
- closing reads the issue first: pull requests are issues in GitHub's API and would be closed
  too, and a second close overwrites the close reason (recorded in M3);
- `duplicate_of` is GitHub's internal issue id, not the number the model knows.

The first four are checks on one call's arguments or result. The last three need extra API calls
and a decision in between.

Options considered:

- **Data only.** Keep what an allowlist can say, drop the rest. Weaker than M1: the model could
  close a pull request, re-close an issue, or create labels by typo.
- **Hand-written tools per connector**, like `tools_local.py`. Full control, but the generator
  does nothing and every connector is a code job.
- **Declarative rules plus named hooks.** The allowlist states argument rules (allowed values,
  enums, text added to a query, words refused) and the result fields kept; an operation that needs
  more names a hook, a small function in the connector's folder that runs around the call.

## Decision

- Each tool in `allowlist.yaml` maps a tool name to an `operationId` and may declare argument
  rules, the result fields kept, MCP annotations (`readOnlyHint`, `idempotentHint`), and a `hook`.
- A hook is a named function in `connectors/<name>/hooks.py`. It receives the validated arguments
  and a client already holding the run's credential, may make extra calls, and returns the result
  or raises a tool error. Hooks cannot widen what the rules allow; the rules run first.
- Hooks may also enrich a tool's description when tools are listed (GitHub: the existing labels).
- A connector without special needs stays data only.
- The allowlist format is a contract (`contracts/connector-allowlist.schema.json`).

## Consequences

- The gateway keeps every M1 guardrail; M4's parity check runs `tools_local.py`'s test cases
  against the gateway.
- "A connector is data" now holds only for simple connectors; ADR 0006's consequence is narrowed,
  not reversed.
- Hooks are code that runs with a real credential: each needs tests with recorded responses, and
  review like any gateway code.
- Rules stay readable in one file per connector; a reviewer sees what an agent may do without
  reading code, except where a hook is named.
