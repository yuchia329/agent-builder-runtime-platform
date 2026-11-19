"""Tools through the tool gateway over MCP (ADR 0006, contracts/mcp-tool-call.schema.json).

Every request carries the run's token in the `X-Run-Token` header (ADR 0003,
contracts/mcp-tool-call.schema.json); a call also sends `_meta` `agentplatform/step` and
`agentplatform/idempotency_key`. `readOnlyHint` and
`idempotentHint` become `ToolSpec.read_only` / `idempotent`; a result is the call's
`structuredContent` (`{"result": x}` unwrapped to `x`); `isError` becomes a `ToolError` with the
gateway's text, which the model may read.

Uses the official MCP SDK client (stateless Streamable HTTP, JSON responses). The worker is
synchronous, so each request opens a short session in its own event loop. No retries here: the
gateway retries the outside API, and whether a tool may be repeated is core's decision
(ADR 0013). On AWS the gateway sits behind API Gateway with IAM: `auth=SignedAuth(signer)` puts
a SigV4 signature in `Authorization` (TOOL_GATEWAY_SIGV4). Unsigned (locally), the token also goes
as `Authorization: Bearer`, until every gateway reads `X-Run-Token` (roadmap, Cross-service
changes). Errors never contain the token.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

import anyio
import httpx2
from mcp.client.client import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError

from worker.adapters.control_plane_http import Signer
from worker.core.errors import ToolError
from worker.core.types import ToolContext, ToolSpec

log = logging.getLogger(__name__)

STEP = "agentplatform/step"
IDEMPOTENCY_KEY = "agentplatform/idempotency_key"
UNAUTHORIZED = -32001  # the gateway's JSON-RPC code for a refused run token (HTTP 401)
TIMEOUT_SECONDS = 60.0  # the gateway retries the outside API itself
RUN_TOKEN_HEADER = "X-Run-Token"
# Headers the signature covers; the token is among them, so it cannot be swapped in transit.
SIGNED_HEADERS = ("host", "content-type", "x-run-token")
SIGNATURE_HEADERS = ("authorization", "x-amz-date", "x-amz-security-token")


class SignedAuth(httpx2.Auth):
    """SigV4 for every request to the gateway (service execute-api), signing method, URL, body and
    `SIGNED_HEADERS` with the signer the control-plane client uses (fresh credentials each time)."""

    requires_request_body = True

    def __init__(self, signer: Signer) -> None:
        self._signer = signer

    def auth_flow(self, request: httpx2.Request):
        headers = {k: v for k, v in request.headers.items() if k.lower() in SIGNED_HEADERS}
        headers.setdefault("host", request.url.netloc.decode())
        signed = self._signer(request.method, str(request.url), headers, request.content)
        for name, value in signed.items():
            if name.lower() in SIGNATURE_HEADERS:
                request.headers[name] = value
        yield request


class GatewayTools:
    def __init__(
        self,
        gateway_url: str,
        *,
        auth: httpx2.Auth | None = None,
        timeout_seconds: float = TIMEOUT_SECONDS,
    ) -> None:
        self._url = gateway_url.rstrip("/") + "/mcp"
        self._auth = auth
        self._timeout = timeout_seconds

    def describe(self, names: Sequence[str], context: ToolContext) -> list[ToolSpec]:
        listed = self._run(context, self._list)
        missing = [n for n in names if n not in listed]
        if missing:
            raise ToolError(f"Unknown tools: {', '.join(missing)}")
        return [listed[n] for n in names]

    def call(self, name: str, arguments: dict[str, Any], context: ToolContext) -> Any:
        if context.step is None or context.idempotency_key is None:
            raise ToolError("Tool call without a step or idempotency key")
        meta = {STEP: context.step, IDEMPOTENCY_KEY: context.idempotency_key}

        async def call(client: Client) -> Any:
            result = await client.call_tool(name, arguments, meta=meta)
            if result.is_error:
                text = " ".join(c.text for c in result.content if getattr(c, "text", None))
                raise ToolError(text or f"{name} failed")
            return _structured(name, result.structured_content)

        return self._run(context, call)

    async def _list(self, client: Client) -> dict[str, ToolSpec]:
        specs: dict[str, ToolSpec] = {}
        cursor: str | None = None
        while True:
            page = await client.list_tools(cursor=cursor)
            for tool in page.tools:
                hints = tool.annotations
                specs[tool.name] = ToolSpec(
                    name=tool.name,
                    description=tool.description or "",
                    input_schema=dict(tool.input_schema),
                    read_only=bool(hints and hints.read_only_hint),
                    idempotent=bool(hints and hints.idempotent_hint),
                )
            cursor = page.next_cursor
            if not cursor:
                return specs

    def _run(self, context: ToolContext, action) -> Any:
        if not context.run_token:
            raise ToolError(
                "This run has no run token, so the tool gateway cannot be used "
                "(the control plane issues one when RUN_TOKEN_PRIVATE_KEY is set)"
            )
        headers = {RUN_TOKEN_HEADER: context.run_token}
        if self._auth is None:  # unsigned: also where gateways before X-Run-Token look
            headers["Authorization"] = f"Bearer {context.run_token}"

        async def session() -> Any:
            async with (
                httpx2.AsyncClient(
                    headers=headers, auth=self._auth, timeout=httpx2.Timeout(self._timeout)
                ) as http,
                Client(streamable_http_client(self._url, http_client=http)) as client,
            ):
                return await action(client)

        try:
            return anyio.run(session)
        except Exception as raised:  # noqa: BLE001 - sorted below; no token in any message
            error = _first(raised)
        if isinstance(error, ToolError):
            raise ToolError(str(error)) from None
        if isinstance(error, MCPError):  # JSON-RPC error: unknown tool, invalid params, token
            if error.code == UNAUTHORIZED:
                raise ToolError("Tool gateway refused the run token (HTTP 401)") from None
            raise ToolError(f"Tool gateway refused the request: {error.message}") from None
        log.warning("run %s: tool gateway request failed: %s", context.run_id, _describe(error))
        raise ToolError(f"Tool gateway unavailable ({_describe(error)})") from None


def _structured(name: str, content: dict[str, Any] | None) -> Any:
    """The tool's JSON: the gateway wraps a non-object result as {"result": ...}."""
    if content is None:
        raise ToolError(f"{name} returned no structured result")
    if set(content) == {"result"}:
        return content["result"]
    return content


def _first(error: BaseException) -> BaseException:
    """The SDK's task groups wrap errors in exception groups; the first leaf is the cause."""
    while isinstance(error, BaseExceptionGroup) and error.exceptions:
        error = error.exceptions[0]
    return error


def _describe(error: BaseException) -> str:
    """A short, token-free description: exception types and HTTP status codes only."""
    if isinstance(error, httpx2.HTTPStatusError):
        return f"HTTP {error.response.status_code}"
    return type(error).__name__
