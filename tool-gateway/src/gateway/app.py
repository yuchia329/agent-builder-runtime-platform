"""The gateway's ASGI app: MCP over Streamable HTTP at /mcp (stateless, JSON responses) behind a
run-token check, plus GET /health and the public login routes under /oauth/ (gateway.oauth).
Conventions: contracts/mcp-tool-call.schema.json."""

import json
import logging
import time
from typing import Any

import anyio
from jsonschema import Draft202012Validator
from mcp.server import Server, ServerRequestContext
from mcp.server.transport_security import TransportSecuritySettings
from mcp.shared.exceptions import MCPError
from mcp.types import (
    CallToolRequestParams,
    CallToolResult,
    ListToolsResult,
    PaginatedRequestParams,
    TextContent,
    ToolAnnotations,
)
from mcp.types import Tool as McpTool

from gateway.approvals import ApprovalCheck
from gateway.auth.run_token import RunClaims, RunTokenVerifier, TokenRejected
from gateway.contracts import Contracts
from gateway.oauth.routes import unavailable_app
from gateway.tools import access_log
from gateway.tools.registry import CallContext, Registry, ToolFailure
from gateway.tracing import TRACEPARENT, Tracing, build_tracing, run_attributes

log = logging.getLogger("gateway")

STEP = "agentplatform/step"
IDEMPOTENCY_KEY = "agentplatform/idempotency_key"
CLAIMS = "run_claims"  # key in the ASGI scope's state
INVALID_PARAMS = -32602
UNAUTHORIZED = -32001


def build_server(
    registry: Registry, contracts: Contracts, approvals: ApprovalCheck, tracing: Tracing
) -> Server:
    call_schema = contracts.validator("mcp-tool-call")

    async def list_tools(ctx: ServerRequestContext, params: PaginatedRequestParams | None):
        claims = _claims(ctx)
        with tracing.span(
            "mcp.tools_list", run_attributes(claims), traceparent=_traceparent(ctx)
        ) as span:
            allowed = registry.allowed(claims)
            span.set_attribute("tools.count", len(allowed))
            descriptions = [await _description(tool, claims) for tool in allowed]
        return ListToolsResult(
            tools=[
                McpTool(
                    name=tool.name,
                    description=description,
                    input_schema=tool.input_schema,
                    annotations=ToolAnnotations(
                        read_only_hint=tool.read_only,
                        idempotent_hint=tool.idempotent,
                        destructive_hint=tool.destructive,
                    ),
                )
                for tool, description in zip(allowed, descriptions, strict=True)
            ]
        )

    async def call_tool(ctx: ServerRequestContext, params: CallToolRequestParams):
        claims = _claims(ctx)
        meta = _meta(params)
        attributes = {
            **run_attributes(claims),
            "gen_ai.tool.name": params.name,
            "run.step": meta.get(STEP) if isinstance(meta.get(STEP), int) else None,
        }
        with tracing.span("tool.execute", attributes, traceparent=_traceparent(ctx)) as span:
            return await _call_tool(claims, params, meta, span)

    async def _call_tool(claims: RunClaims, params: CallToolRequestParams, meta, span):
        started = time.monotonic()
        arguments = params.arguments or {}
        step, key = meta.get(STEP), meta.get(IDEMPOTENCY_KEY)

        def done(outcome: str, reason: str | None = None) -> None:
            span.set_attribute("tool.outcome", outcome)
            elapsed = (time.monotonic() - started) * 1000
            access_log.record(claims, params.name, step, key, arguments, outcome, elapsed, reason)

        tool = registry.get(params.name) if params.name in claims.tools else None
        if tool is None:
            done("refused", "tool not allowed for this run")
            raise MCPError(INVALID_PARAMS, f"Unknown tool: {params.name}")
        errors = [
            e.message
            for e in call_schema.iter_errors(
                {"name": params.name, "arguments": arguments, "_meta": meta}
            )
        ]
        if errors:
            done("refused", "call params do not follow the contract")
            raise MCPError(INVALID_PARAMS, "Invalid tools/call params: " + "; ".join(errors[:3]))
        argument_errors = sorted(
            Draft202012Validator(tool.input_schema).iter_errors(arguments), key=lambda e: e.path
        )
        if argument_errors:
            done("error", "invalid arguments")
            return _error("Invalid arguments: " + "; ".join(e.message for e in argument_errors[:5]))
        if params.name in claims.require_approval:
            # Second approval check (ADR 0003): blocking HTTP, so off the event loop.
            with tracing.span("approval.check") as check:
                refusal = await anyio.to_thread.run_sync(
                    approvals.check, claims, params.name, step, arguments
                )
                check.set_attribute("approval.outcome", "refused" if refusal else "approved")
            if refusal is not None:
                done("refused", refusal)
                return _error(
                    f"{params.name} needs a person's approval for this exact call; "
                    "no matching approval was found"
                )
        try:
            # Tools make blocking HTTP calls; keep them off the event loop.
            result = await anyio.to_thread.run_sync(
                tool.run, arguments, CallContext(claims, step, key)
            )
        except ToolFailure as e:
            done("error", "tool failure")
            return _error(str(e))
        except Exception:
            log.exception("Tool %s failed in run %s", params.name, claims.run_id)
            done("error", "unexpected failure")
            return _error(f"{params.name} failed unexpectedly")
        done("ok")
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(result, sort_keys=True))],
            structured_content=result if isinstance(result, dict) else {"result": result},
        )

    return Server("tool-gateway", on_list_tools=list_tools, on_call_tool=call_tool)


async def _description(tool, claims: RunClaims) -> str:
    """The tool's description plus what its hook adds for this run. Listing never fails because
    of the outside API: on any error the plain description is used."""
    if tool.describe is None:
        return tool.description
    try:
        extra = await anyio.to_thread.run_sync(tool.describe, claims)
    except Exception as e:  # ToolFailure (no credential, API down) or a bug in a hook
        log.warning(
            "Description of %s for run %s not extended: %s",
            tool.name,
            claims.run_id,
            type(e).__name__,
        )
        return tool.description
    return f"{tool.description}\n\n{extra}" if extra else tool.description


def _traceparent(ctx: ServerRequestContext) -> str | None:
    """The caller's `traceparent` header (the worker's span); /mcp is internal (IAM on AWS), so
    it may continue the caller's trace (ADR 0022)."""
    headers = getattr(ctx.request, "headers", None)
    return headers.get(TRACEPARENT) if headers is not None else None


def _claims(ctx: ServerRequestContext) -> RunClaims:
    claims = getattr(getattr(ctx.request, "state", None), CLAIMS, None)
    if claims is None:  # the auth wrapper always sets it; never trust a request without
        raise MCPError(UNAUTHORIZED, "Unauthorized")
    return claims


def _meta(params: CallToolRequestParams) -> dict[str, Any]:
    if params.meta is None:
        return {}
    if isinstance(params.meta, dict):
        return dict(params.meta)
    return params.meta.model_dump(by_alias=True, exclude_none=True)


def _error(message: str) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=message)], is_error=True)


class RunTokenAuth:
    """ASGI wrapper: /mcp needs a valid run token (claims put in the scope's state for the
    handlers); /health and /oauth/ (a person's browser) answer without one. A refusal is HTTP 401
    with a JSON-RPC error body and no detail; the reason goes to the log, the token never does."""

    def __init__(self, app, verifier: RunTokenVerifier, oauth=None) -> None:
        self._app = app
        self._verifier = verifier
        self._oauth = oauth or unavailable_app()

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            return await self._app(scope, receive, send)  # lifespan
        if scope["path"] == "/health" and scope["method"] == "GET":
            return await _respond(send, 200, {"status": "ok"})
        if scope["path"].startswith("/oauth/"):
            return await self._oauth(scope, receive, send)
        if scope["path"].rstrip("/") == "/mcp":
            token = _run_token(scope)
            try:
                if token is None:
                    raise TokenRejected("no run token")
                claims = self._verifier.verify(token)
            except TokenRejected as e:
                log.info("Refused /mcp request: %s", e)
                return await _respond(
                    send,
                    401,
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": UNAUTHORIZED, "message": "Unauthorized"},
                    },
                    [(b"www-authenticate", b"Bearer")],
                )
            scope.setdefault("state", {})[CLAIMS] = claims
        return await self._app(scope, receive, send)


def _run_token(scope) -> str | None:
    """The run token (ADR 0020): `X-Run-Token` when present, and then alone (on AWS
    `Authorization` holds the caller's SigV4 signature); otherwise `Authorization: Bearer`, the
    old place, accepted until every worker sends `X-Run-Token` only. Empty means none."""
    headers = dict(scope.get("headers", []))
    if b"x-run-token" in headers:
        return headers[b"x-run-token"].decode("latin-1").strip() or None
    scheme, _, token = headers.get(b"authorization", b"").decode("latin-1").partition(" ")
    return token.strip() or None if scheme.lower() == "bearer" else None


async def _respond(send, status: int, body: dict, headers: list | None = None) -> None:
    payload = json.dumps(body).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(payload)).encode()),
                *(headers or []),
            ],
        }
    )
    await send({"type": "http.response.body", "body": payload})


def create_app(
    registry: Registry,
    verifier: RunTokenVerifier,
    contracts: Contracts,
    approvals: ApprovalCheck | None = None,
    oauth=None,
    tracing: Tracing | None = None,
):
    """`approvals` None: every require_approval tool is refused (fail closed). `oauth`: the login
    routes (`gateway.oauth.routes.oauth_app`); None answers 503 there. `tracing` None: spans are
    made and dropped (`TRACING_EXPORTER=none`)."""
    server = build_server(
        registry, contracts, approvals or ApprovalCheck(None), tracing or build_tracing()
    )
    mcp_app = server.streamable_http_app(
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
        # Every request needs a run token, so DNS rebinding (a browser tricked into calling
        # localhost) gains nothing; the check would refuse container host names.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    return RunTokenAuth(mcp_app, verifier, oauth)
