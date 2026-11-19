# Agent Builder & Runtime Platform

A multi-tenant platform where a team creates an AI agent by writing a declarative spec file, and
the platform runs that agent reliably whenever its trigger fires.

- **Control plane** (Java, Spring Boot) versions agent specs and turns events into runs.
- **Workers** (Python) run each agent's loop on any configured model provider (MiniMax, OpenAI,
  OpenRouter, Gemini, Amazon Bedrock, a self-hosted server), checking every model reply against
  the spec and budget, and checkpointing every step so a crashed worker resumes without repeating
  side effects.
- **Tool gateway** (Python, MCP) exposes real APIs as tools and is the only component that holds
  credentials.
- Risky actions wait for human approval in Slack. Every spec can be scored by an eval suite (`make eval`) before a change goes live.

Demo agent: a GitHub issue triage agent that labels new issues, finds duplicates, posts a summary
to Slack, and asks a human before closing anything.

## Status

M0 (local skeleton) done: publish a spec and run it end to end on your machine with a stub model.
Next: M1, a real agent on a real model (MiniMax first). See the [roadmap](docs/roadmap.md).

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
worker/          Python 3.12, own agent loop
tool-gateway/    Python, FastAPI + MCP
specs/           example agent specs
evals/           regression suite and CI gate
loadtest/        stub model, load driver, chaos script
infra/           Terraform
local-stack/     local AWS stand-ins and the demo script
docs/            product, architecture, roadmap, ADRs
```

## Quickstart

Needs Docker (with Compose) and `make`; nothing else is installed on your machine.

```sh
make up     # build and start everything; the first build takes a few minutes
make demo   # publish the example spec, start a run, wait until it SUCCEEDED
make down   # stop everything and delete local data
```

`make up` starts DynamoDB Local and ElasticMQ (local stand-ins for DynamoDB and SQS), the control
plane on `localhost:8080`, and one worker. The worker uses a stub model and fake tools, so no AWS
account or API key is needed. `make demo` prints each step and the finished run; `make logs`
follows all containers. Commands for each service: [AGENTS.md](AGENTS.md) → Commands.
