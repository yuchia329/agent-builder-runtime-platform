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
| Owner login (acts_as owner) | add `GITHUB_OAUTH_CLIENT_ID`, `GITHUB_OAUTH_CLIENT_SECRET`, `OAUTH_STATE_SECRET` to `../.env` (`.env.example`); the App's redirect URI is `http://localhost:8090/oauth/github/callback`; an owner run's tool error gives the link to open; logins are lost on restart (memory store) |
| Point a connector at a simulator | `CONNECTOR_<NAME>_BASE_URL=http://127.0.0.1:<port>` (evals and tests; loopback only; startup logs it) |
| Stop a background gateway | `kill $(lsof -ti :8090)` (not `pkill -f gateway`: it matches the shell too) |
| Lambda zip | `uv run python scripts/lambda_zip.py` (`build/gateway-lambda.zip`, arm64 wheels; ADR 0019) |
| Lambda trial (AWS, ~2 min, ~$0) | `uv run python scripts/lambda_trial.py` (throwaway function with the Web Adapter layer: `/health`, 401, `initialize` with a KMS-signed token; deletes everything; profile `default`) |
| GitHub App credential into SSM (AWS) | `uv run python scripts/put_app_credential.py --status`, then without `--status` (from `../.env`; never prints the key) |
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
- `oauth/`: owner login (ADR 0015): `signing.py` (signed tickets and state, PKCE verifier
  derived from a nonce), `github.py` (provider HTTP), `logins.py` (`OAuthLogins`: link, `begin`,
  `finish`, `headers` with refresh), `routes.py` (Starlette app mounted by `RunTokenAuth` at
  `/oauth/`).
- `../connectors/<name>/`: `openapi.yaml` (cut, never edited by hand), `allowlist.yaml`, optional
  `hooks.py` with a `HOOKS` dict (loaded by `openapi_to_mcp.load_hooks`).
- `ports/` (`Protocol` only) and `adapters/`; `src/gateway/connectors/fake.py` (in code, a test
  double).
- `tests/fake_github.py` + `tests/fixtures/github/`: recorded GitHub bodies copied from the
  worker's fixtures (see its README); routes with `on`, `fail_next` for retries. It answers the
  App token exchange itself (`token_requests`, `fail_token_next`, `app_token`), so `requests` holds
  only API calls; `app_store()` / `app_credential()` give the matching App credential.
- `tests/fake_github_oauth.py`: GitHub's login (authorize auto-approves or `deny`; token
  endpoint checks client secret and PKCE, rotates refresh tokens; `/user`); `LiveGateway(...,
  oauth=fn)` takes a function from the gateway URL to the login routes.
- `tests/fake_control_plane.py`: answers `GET /internal/runs/{id}` from runs put with `put`;
  `fail_next`, `garbage`.
- `contracts.py` loads every `../contracts/*.schema.json` together (`CONTRACTS_DIR` to move).
- AWS (ADR 0018, 0019): `aws.py` (`aws_env` fills the environment from KMS and SSM before
  `Settings.from_env`; `sigv4_signer`), `adapters/token_store_ssm.py`, `run.sh` (Lambda handler,
  run by the Web Adapter layer), `scripts/lambda_zip.py`, `scripts/lambda_trial.py`,
  `scripts/put_app_credential.py`. `tests/test_aws.py` and the SSM store's contract run against
  moto (AWS imitated in process; no network).
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
- Login routes keep no server state (Lambda): everything between `/start` and `/callback` is in
  signed notes and the nonce cookie. A note's purpose (`ticket`, `state`) is checked; derived
  values use their own label. Never put a ticket, state, code or token in a page or a log line
  (uvicorn's request log goes through `HideLoginQueries`).
- A connector's address comes from its allowlist's `base_url`; the only override is
  `CONNECTOR_<NAME>_BASE_URL` (`config.connector_base_url`), loopback only. Do not widen it: the
  override receives the connector's credentials.
- `Settings` fields holding secrets use `field(repr=False)`; `Settings.env` holds the whole
  environment and is hidden too.
- AWS lookups happen only in `aws.aws_env` at startup, and only when their setting is given;
  `Settings.from_env` stays pure. A new gateway secret: add it to `aws.SECRET_PARAMETERS` and to
  `infra/envs/dev/ssm.tf`.
- The SSM `TokenStore` never caches: another Lambda environment may have rotated a refresh token.
  Values stay under 4 KB (standard tier); errors name the parameter, never the value.
- moto tests set fake `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` (monkeypatch) and build
  clients inside `mock_aws()`. moto's KMS `GetPublicKey` answers only `CustomerMasterKeySpec`
  (real KMS answers `KeySpec` too); `aws.kms_public_key` accepts either.
