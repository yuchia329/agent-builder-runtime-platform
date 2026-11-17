# Agent Builder & Runtime Platform

A multi-tenant platform where a team creates an AI agent by writing a declarative spec file, and
the platform runs that agent reliably whenever its trigger fires.

- **Control plane** (Java, Spring Boot) versions agent specs and turns events into runs.
- **Workers** (Python, Strands Agents) execute runs on Amazon Bedrock, checkpointing every step so
  a crashed worker resumes without repeating side effects.
- **Tool gateway** (Python, MCP) exposes real APIs as tools and is the only component that holds
  credentials.
- Risky actions wait for human approval in Slack. Every spec change is gated by an eval suite in CI.

Demo agent: a GitHub issue triage agent that labels new issues, finds duplicates, posts a summary
to Slack, and asks a human before closing anything.

## Status

Pre-M0. Design is in place; no code yet. See the [roadmap](docs/roadmap.md).

## Documentation

| Doc                                       | Read it for                                                     |
| ----------------------------------------- | --------------------------------------------------------------- |
| [Product](docs/product.md)                | What the platform does, concepts, use cases, workflows          |
| [Architecture](docs/architecture.md)      | Components, data flow, security, configuration, repo layout     |
| [Roadmap](docs/roadmap.md)                | Milestones, current status, next tasks, open questions          |
| [Decision records](docs/adr/README.md)    | Why each design choice was made                                 |

## Repository

```
contracts/       shared schemas between services
control-plane/   Java 21, Spring Boot
worker/          Python, Strands Agents
tool-gateway/    Python, FastAPI + MCP
specs/           example agent specs
evals/           regression suite and CI gate
loadtest/        stub model, load driver, chaos script
infra/           Terraform
docs/            product, architecture, roadmap, ADRs
```

## Quickstart

Not available yet. Arrives with milestone M0 (`docker compose up`, publish a spec, run one agent
end to end against a stub model).
