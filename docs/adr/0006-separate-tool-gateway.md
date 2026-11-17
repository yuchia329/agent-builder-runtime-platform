# 0006. Tool gateway as a separate service, built in stages

- Status: Accepted
- Date: 2026-11-16

## Context

Agents call outside APIs (GitHub, Slack, Notion, Google Calendar) using stored credentials. The
worker runs model-driven code, which is the part most exposed to prompt injection. If tool calls
run inside the worker, the worker process holds every credential.

## Decision

- Tool calls go through a separate service, the tool gateway. It holds credentials (encrypted with
  KMS; only its IAM role can decrypt), generates MCP tools from each connector's OpenAPI spec and
  allowlist, validates arguments, and calls the API.
- Workers reach it through the `ToolClient` port over MCP.
- Built in stages: in M1, `tools_local.py` calls GitHub directly from the worker with a personal
  token; in M4, the worker switches to `tools_mcp.py` and the gateway service takes over. The agent
  loop does not change.

## Consequences

- The worker and the model never see a credential.
- Adding a connector is data (`openapi.yaml`, `allowlist.yaml`), not code.
- Any MCP client can reuse the same tools.
- One more service to deploy and one more network hop per tool call.
