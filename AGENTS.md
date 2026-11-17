# Agent Builder & Runtime Platform

Instructions for AI coding agents (Claude Code, Cursor, others). This file is the single source;
tool-specific files only point here.

Multi-tenant platform: teams publish a declarative agent spec; a Java control plane versions it and
turns trigger events into runs; Python workers execute runs on any configured model provider
(MiniMax, OpenAI-compatible APIs, Amazon Bedrock, self-hosted; ADR 0010); a Python tool
gateway is the only path to outside APIs. Everything scales to zero when idle: control plane and
gateway on Lambda, workers on Fargate scaled to 0 (ADR 0007). See README.md for the overview.

## Components

Work is organized by component. The list of components, their folders and purposes is in
`docs/architecture.md` → Components. Use those names when starting a session.

## Read before working

1. `docs/roadmap.md`: current status, next tasks, handoff notes, cross-service changes. Always.
2. The `docs/architecture.md` sections for the component you are touching.
3. `docs/adr/`: any ADR about the boundary or behavior you are changing.
4. `contracts/`: the shapes your component sends or receives.
5. The component's own `AGENTS.md` / `README.md`, if present.

Use the terms in `docs/product.md` (Concepts) for names in code: tenant, agent, spec, version, run,
step, tool, connector, `acts_as`, approval, budget.

## Boundaries

Do not break these without a new ADR.

- Only `control-plane/` touches the control-plane DynamoDB table. Workers use its internal API
  (ADR 0001, ADR 0008).
- Only `worker/` touches the run-state DynamoDB table and calls model providers (ADR 0010).
- Only `tool-gateway/` holds credentials and calls outside APIs (ADR 0006).
- Services talk only through shapes defined in `contracts/`: the SQS run message, the internal run
  API, MCP calls with a signed run token (ADR 0003).
- `worker/src/worker/core/` imports no AWS or vendor SDK. Vendor code lives in `adapters/` behind a
  port in `ports/`, and every adapter passes that port's contract tests (ADR 0004).

## Working rules

- One session works on one component and one roadmap task. Do not edit another service's folder.
  If a contract must change, edit `contracts/` and record it under "Cross-service changes" in
  `docs/roadmap.md`, so the other service's next session picks it up.
- Write or update tests with the code. New adapters must pass the port's contract suite.
- Keep each fact in one doc:
  - progress, next steps, handoff → `docs/roadmap.md`
  - how a component works → `docs/architecture.md`
  - a design decision → new `docs/adr/NNNN-*.md` (never edit an accepted ADR; supersede it)
  - what the product does, vocabulary → `docs/product.md`
  - component commands and conventions → that component's `AGENTS.md`
  - agent instructions → `AGENTS.md` files only, never tool-specific files
- `notes/` is private and git-ignored. Do not reference it from tracked files.
- Do not commit unless asked. End every session with the wrap-up procedure below.

## Session start

When asked to start work on a component (optionally with a task):

1. Read everything under "Read before working" for that component.
2. If no task was given, pick the next unchecked roadmap task for this component.
3. Reply with a short brief: current state (from roadmap and code), the task and what "done"
   means, files you expect to touch, contracts involved and whether any must change, open
   questions or conflicts between docs and code.
4. Wait for confirmation before editing files.

## Session wrap-up

So the next session, which starts with no memory, can continue:

1. Run the component's tests and report the result honestly, including failures.
2. Update `docs/roadmap.md`: tick finished tasks and milestone status; add one entry at the top of
   "Handoff notes" (date, component, done, next, gotchas); list any `contracts/` change under
   "Cross-service changes" with the services that must follow.
3. If a design decision was made, draft a new ADR (and add it to the index) and update the matching
   section of `docs/architecture.md`.
4. If you learned build, test or run commands or conventions for the component, add them to its
   `AGENTS.md` (create it if missing, keep it short, and add a `CLAUDE.md` beside it containing
   only `@AGENTS.md`) and to the Commands table below.
5. Summarize doc changes and propose a commit message. Do not commit unless asked.

## Commands

Filled in as each component is scaffolded.

| Component     | Build | Test | Run locally |
| ------------- | ----- | ---- | ----------- |
| contracts     | —     | `cd contracts && uv run pytest` | — |
| control-plane | `cd control-plane && ./gradlew bootJar` | `cd control-plane && ./gradlew test` (needs Docker) | `cd control-plane && ./gradlew bootRun` |
| worker        | `cd worker && uv sync` | `cd worker && uv run pytest` (needs Docker; `-m "not docker"` without) | `cd worker && set -a && . ../.env && set +a && uv run worker` |
| tool-gateway  | TBD   | TBD  | TBD         |
| specs         | — | validate: see `specs/AGENTS.md` | real run: `specs/README.md` |
| local-stack   | `make up` (builds both images) | `make up && make demo` | `make up` / `make stack` / `make down` (repo root; see `local-stack/AGENTS.md`) |
