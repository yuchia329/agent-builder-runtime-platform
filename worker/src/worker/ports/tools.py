from collections.abc import Sequence
from typing import Any, Protocol

from worker.core.types import ToolContext, ToolSpec


class ToolClient(Protocol):
    """Runs tools. Adapters: fake (tests), direct API calls (M1), MCP gateway (M4).

    Core passes the run's ``ToolContext`` on every request; adapters that need no run identity
    ignore it."""

    def describe(self, names: Sequence[str], context: ToolContext) -> list[ToolSpec]:
        """Schemas for these tools, in the same order. Raises ToolError for an unknown name."""
        ...

    def call(self, name: str, arguments: dict[str, Any], context: ToolContext) -> Any:
        """Runs one tool and returns a JSON-compatible result. Raises ToolError on failure."""
        ...
