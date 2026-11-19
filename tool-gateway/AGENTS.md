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
| Run with GitHub | `set -a && . ../.env && set +a && CONNECTORS=github uv run gateway` (needs `RUN_TOKEN_PUBLIC_KEY`, `GITHUB_REPOS`, `TENANT_ID` and the `GITHUB_APP_*` settings; startup logs "Credential from the environment"; `CONTROL_PLANE_URL` from `.env` enables approval tools) |
| Stop a background gateway | `kill $(lsof -ti :8090)` (not `pkill -f gateway`: it matches the shell too) |
| Lint | `uv run ruff check . && uv run ruff format --check .` |
| Format | `uv run ruff format .` |
| Re-cut a connector's openapi.yaml | see the docstring of `scripts/cut_openapi.py` (download to its own folder, run with `python -I`) |
| Tests hang at 0% CPU | iCloud evicted `.venv` files: `find .venv -flags +dataless \| wc -l`; if not 0, `rm -rf .venv && uv sync` |

## Layout

- `src/gateway/app.py`: the MCP server (low-level `mcp.server.Server`, SDK 2.x: handlers are
  `on_list_tools` / `on_call_tool` constructor arguments, `ctx.request` is the Starlette request)
  and `RunTokenAuth`, the ASGI wrapper that checks the token and puts the claims in the scope's
  `state` (`run_claims`).
- `auth/run_token.py`: `RunTokenVerifier`, `thumbprint` (RFC 7638; checked equal to Nimbus's,
  which the control plane uses).
- `tools/registry.py`: `Tool`, `Connector`, `Registry`, `ToolFailure`; `tools/access_log.py`.
- `tools/openapi_to_mcp.py` (generator and load-time checks), `tools/rules.py` (pure rule
  functions), `tools/executor.py` (`ConnectorClient`: one per generated connector, `HookCall`),
  `credentials.py` (which stored credential a call uses; returns an `Auth` the executor renews
  once on a 401), `github_app.py` (app JWT, installation token exchange and cache), `seed.py`
  (local credentials from the environment into the memory `TokenStore`), `approvals.py` (second
  approval check) with the `ControlPlane` port (`adapters/control_plane_http.py`, `_memory.py`).
- `../connectors/<name>/`: `openapi.yaml` (cut, never edited by hand), `allowlist.yaml`, optional
  `hooks.py` with a `HOOKS` dict (loaded by `openapi_to_mcp.load_hooks`).
- `ports/` (`Protocol` only) and `adapters/`; `src/gateway/connectors/fake.py` (in code, a test
  double).
- `tests/fake_github.py` + `tests/fixtures/github/`: recorded GitHub bodies copied from the
  worker's fixtures (see its README); routes with `on`, `fail_next` for retries. It answers the
  App token exchange itself (`token_requests`, `fail_token_next`, `app_token`), so `requests` holds
  only API calls; `app_store()` / `app_credential()` give the matching App credential.
- `tests/fake_control_plane.py`: answers `GET /internal/runs/{id}` from runs put with `put`;
  `fail_next`, `garbage`.
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
- Fail closed: a tool in the token's `require_approval` runs only after `approvals.ApprovalCheck`
  finds an exact approved match in the run (`CONTROL_PLANE_URL`); every other outcome, including
  errors, refuses. Keep `canonical()` equal to the worker's `_canonical` in `core/handle_run.py`.
- DNS-rebinding protection is off on purpose (container host names; the token protects `/mcp`).
- Tests talk to a real server through the SDK client (`LiveGateway`, `mcp_client`); async tests
  use `pytest.mark.anyio`.
- The fake connector mirrors the worker's `tools_fake.issue_triage_tools()` (names, schemas,
  flags, results); change both together.
- A new adapter joins its port's `ADAPTERS` list and passes the suite.
- Tools expose the API's own parameter names; never rename arguments in a connector. A rule
  only narrows (an `enum` wider than the API's fails load).
- Every load-time rule the contract cannot express gets a case in
  `tests/test_openapi_to_mcp.py::BREAKS`.
- Hooks get a `HookCall`: arguments after rules, `hook_arguments`, `send` (the tool's own request)
  and `operation` (only `hook_operations`), and `settings`; they return the raw API response, and
  the result shape is applied after. Model-readable failures raise `ToolFailure`; catch
  `ApiError` to act on a status (404 to "does not exist").
- A hook function may carry a `describe` attribute (`DescribeCall`: claims, operation, settings)
  returning text added to the tool description at `tools/list`. It must be safe to fail:
  `app.py` logs and lists the plain description.
- `tests/test_github_parity.py` maps every test in `worker/tests/unit/test_tools_local.py` to a
  gateway test; a new worker test needs a map entry and a gateway equivalent.
- A credential kind's errors name what is wrong (missing field, rejected with status), never a
  key, JWT or token; `AppCredential`'s repr hides the key.
- Recorded fixtures are cleaned like the worker's (owner/repo `octocat/Hello-World`, no users,
  avatars, node ids or bodies); check with `grep -ri <your-login> tests/fixtures` before
  committing a new recording.
