# tool-gateway

Python 3.12, uv. Holds credentials and runs agent tool calls over MCP (ADR 0006). Design:
`docs/architecture.md` → Tool gateway, Security; ADR 0003 (run token), ADR 0014 (connector rules
and hooks). Shapes: `contracts/run-token.schema.json`, `mcp-tool-call.schema.json`,
`connector-allowlist.schema.json`.

## Commands

Run from `tool-gateway/`.

| What | Command |
| --- | --- |
| Install | `uv sync` |
| Test | `uv run pytest` (no Docker) |
| Run locally | `RUN_TOKEN_PUBLIC_KEY=<public half of the control plane's pair> CONNECTORS=fake uv run gateway` (port 8090; `GATEWAY_PORT` to change) |
| Lint | `uv run ruff check . && uv run ruff format --check .` |
| Format | `uv run ruff format .` |
| Tests hang at 0% CPU | iCloud evicted `.venv` files: `find .venv -flags +dataless \| wc -l`; if not 0, `rm -rf .venv && uv sync` |

## Layout

- `src/gateway/app.py`: the MCP server (low-level `mcp.server.Server`, SDK 2.x: handlers are
  `on_list_tools` / `on_call_tool` constructor arguments, `ctx.request` is the Starlette request)
  and `RunTokenAuth`, the ASGI wrapper that checks the token and puts the claims in the scope's
  `state` (`run_claims`).
- `auth/run_token.py`: `RunTokenVerifier`, `thumbprint` (RFC 7638; checked equal to Nimbus's,
  which the control plane uses).
- `tools/registry.py`: `Tool`, `Connector`, `Registry`, `ToolFailure`; `tools/access_log.py`.
- `ports/` (`Protocol` only) and `adapters/`; `connectors/fake.py` (in code, a test double).
- `contracts.py` loads every `../contracts/*.schema.json` together (`CONTRACTS_DIR` to move).
- `tests/gateway_testing.py`: keys, signed tokens, `LiveGateway` (the real app on a free port in a
  thread), `mcp_client` (the SDK's client with a bearer header). `tests/contract/`: one suite per
  port, parametrized over adapters.

## Conventions

- Every `/mcp` request needs a run token. Never log a token, a key, or argument values; the
  access log (`gateway.access`, one JSON line per call) has a SHA-256 of the arguments instead.
- A refused token is HTTP 401 with no reason for the caller; the reason goes to the log.
- Messages a model may read come from `ToolFailure` or return `isError`; anything unexpected is
  logged and answered "<tool> failed unexpectedly". No secret in any message.
- SDK 2.x does not validate tool arguments: `app.py` checks them against the tool's
  `input_schema`, and the call params against the contract, before a tool runs.
- Fail closed: a tool in the token's `require_approval` is refused until the approval check
  (M4 task 7) is in place.
- DNS-rebinding protection is off on purpose (container host names; the token protects `/mcp`).
- Tests talk to a real server through the SDK client (`LiveGateway`, `mcp_client`); async tests
  use `pytest.mark.anyio`.
- The fake connector mirrors the worker's `tools_fake.issue_triage_tools()` (names, schemas,
  flags, results); change both together.
- A new adapter joins its port's `ADAPTERS` list and passes the suite.
