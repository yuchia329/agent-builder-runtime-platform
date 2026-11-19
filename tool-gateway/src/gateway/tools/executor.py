"""Runs a generated tool: checks and rules, the hook if any, the HTTP call, the result shape.

Retries 5xx answers and network errors (3 attempts); a rate limit is reported, not retried; a 4xx
answer reaches the model as "<connector> answered <status>: <API message>". No credential ever
appears in a result or error.
"""

import time
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx2

from gateway.auth.run_token import RunClaims
from gateway.credentials import Auth, Credentials
from gateway.tools import rules
from gateway.tools.openapi_to_mcp import ConnectorDefinition, Operation, ToolDefinition
from gateway.tools.registry import ApiError, CallContext, Tool, ToolFailure

ATTEMPTS = 3
TIMEOUT_SECONDS = 10.0


@dataclass
class HookCall:
    """What a hook gets: the arguments (after rules), and ways to run the tool's own request or a
    declared hook operation. A hook returns the API response the result shape applies to."""

    arguments: dict[str, Any]
    hook_arguments: dict[str, Any]
    context: CallContext
    send: Callable[[dict[str, Any]], Any]
    operation: Callable[[str, dict[str, Any]], Any]
    settings: dict[str, list[str]]


@dataclass
class DescribeCall:
    """What a hook's `describe` gets at tools/list: the settings and the declared hook operations,
    with the run's credential. It returns extra description text, or None."""

    claims: RunClaims
    operation: Callable[[str, dict[str, Any]], Any]
    settings: dict[str, list[str]]


class ConnectorClient:
    def __init__(
        self,
        connector: ConnectorDefinition,
        credentials: Credentials,
        base_url: str | None = None,
        sleep: Callable[[float], None] = time.sleep,
        http: httpx2.Client | None = None,
    ) -> None:
        self.connector = connector
        self._credentials = credentials
        self._base_url = (base_url or connector.base_url).rstrip("/")
        self._sleep = sleep
        self._http = http or httpx2.Client(timeout=TIMEOUT_SECONDS)

    def tools(self) -> list[Tool]:
        return [
            Tool(
                name=d.name,
                description=d.description,
                input_schema=d.input_schema,
                run=self._runner(d),
                read_only=d.read_only,
                idempotent=d.idempotent,
                destructive=d.destructive,
                describe=self._describer(d),
            )
            for d in self.connector.tools.values()
        ]

    def _describer(self, tool: ToolDefinition) -> Callable[[RunClaims], str | None] | None:
        describe = getattr(tool.hook, "describe", None)
        if describe is None:
            return None

        def run(claims: RunClaims) -> str | None:
            auth = self._auth(claims)
            return describe(
                DescribeCall(claims, self._hook_operation(auth), self.connector.settings)
            )

        return run

    def _auth(self, claims: RunClaims) -> Auth:
        return self._credentials.auth(
            self.connector.name, self.connector.credentials, claims, self._base_url
        )

    def _hook_operation(self, auth: Auth) -> Callable[[str, dict[str, Any]], Any]:
        def operation(operation_id: str, values: dict[str, Any]) -> Any:
            if operation_id not in self.connector.hook_operations:
                raise ToolFailure(f"Operation {operation_id} is not allowed for hooks")
            return self._request(self.connector.operations[operation_id], values, auth)

        return operation

    @property
    def name(self) -> str:
        return self.connector.name

    def _runner(self, tool: ToolDefinition) -> Callable[[dict[str, Any], CallContext], Any]:
        def run(arguments: dict[str, Any], context: CallContext) -> Any:
            return self.call(tool, arguments, context)

        return run

    def call(self, tool: ToolDefinition, arguments: dict[str, Any], context: CallContext) -> Any:
        settings = self.connector.settings
        api_args = {k: v for k, v in arguments.items() if k not in tool.hook_arguments}
        hook_args = {k: v for k, v in arguments.items() if k in tool.hook_arguments}
        rules.check_values(tool.checks, {**tool.fixed, **api_args}, settings)
        api_args = rules.apply_argument_rules(api_args, tool.argument_rules, settings)
        auth = self._auth(context.claims)

        def send(values: dict[str, Any]) -> Any:
            return self._request(tool.operation, {**tool.fixed, **values}, auth)

        if tool.hook is not None:
            call = HookCall(
                api_args, hook_args, context, send, self._hook_operation(auth), settings
            )
            response = tool.hook(call)
        else:
            response = send(api_args)
        return rules.shape_result(response, tool.result)

    def _request(self, op: Operation, values: dict[str, Any], auth: Auth) -> Any:
        path = op.path
        query: dict[str, Any] = {}
        body: dict[str, Any] = {}
        for name, value in values.items():
            param = op.parameters.get(name)
            if param is not None and param["in"] == "path":
                path = path.replace("{" + name + "}", urllib.parse.quote(str(value), safe=""))
            elif param is not None:
                query[name] = value
            else:
                body[name] = value
        if "{" in path:
            raise ToolFailure(f"Missing path argument for {op.operation_id}")
        request = {
            "method": op.method,
            "url": self._base_url + path,
            "params": query or None,
            "json": body if (body or op.method in ("POST", "PATCH", "PUT")) and op.body else None,
        }
        name = self.connector.name
        renewed = False
        attempt = 0
        while attempt < ATTEMPTS:
            attempt += 1
            headers = {**self.connector.headers, **auth.headers}
            try:
                response = self._http.request(**request, headers=headers)
            except httpx2.TransportError:
                if attempt == ATTEMPTS:
                    raise ToolFailure(f"{name} is unreachable; try again later") from None
                self._sleep(attempt)
                continue
            if response.status_code >= 500:
                if attempt == ATTEMPTS:
                    raise ToolFailure(f"{name} is unavailable ({response.status_code}); try later")
                self._sleep(attempt)
                continue
            if response.status_code == 429 or (
                response.status_code == 403 and response.headers.get("x-ratelimit-remaining") == "0"
            ):
                raise ToolFailure(f"{name} rate limit reached; try again later")
            if response.status_code == 401 and not renewed and auth.renew():
                renewed = True  # a cached credential revoked early: once, not counted
                attempt -= 1
                continue
            if response.status_code >= 400:
                status = response.status_code
                raise ApiError(f"{name} answered {status}: {_message(response)}", status)
            return response.json() if response.content else {}
        raise AssertionError("unreachable")


def _message(response: httpx2.Response) -> str:
    try:
        message = response.json().get("message", "")
    except ValueError:
        message = ""
    return (str(message) or response.reason_phrase or "error")[:300]
