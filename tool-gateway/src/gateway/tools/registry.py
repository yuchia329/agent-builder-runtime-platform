"""Tools the gateway can run, gathered from the enabled connectors."""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, Protocol

from gateway.auth.run_token import RunClaims


class ToolFailure(Exception):
    """A failure the model may read and react to (MCP result with isError). Never put a secret
    in the message."""


class ApiError(ToolFailure):
    """The outside API answered with a client error (`status`); hooks may turn it into a clearer
    message (a 404 into "#n does not exist")."""

    def __init__(self, message: str, status: int) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class CallContext:
    claims: RunClaims
    step: int
    idempotency_key: str


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any]
    run: Callable[[dict[str, Any], CallContext], Any]
    read_only: bool = False
    idempotent: bool = False
    destructive: bool = False
    # Extra description text for one run, computed at tools/list (may call the API); None = none.
    describe: Callable[[RunClaims], str | None] | None = None


class Connector(Protocol):
    name: str

    def tools(self) -> list[Tool]: ...


class Registry:
    def __init__(self, connectors: Iterable[Connector]) -> None:
        self._tools: dict[str, Tool] = {}
        for connector in connectors:
            for tool in connector.tools():
                if tool.name in self._tools:
                    raise ValueError(f"Tool {tool.name} is defined twice")
                self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def allowed(self, claims: RunClaims) -> list[Tool]:
        """Tools in both the token and the loaded connectors, by name."""
        return [self._tools[n] for n in sorted(claims.tools) if n in self._tools]
