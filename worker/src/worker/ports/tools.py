from collections.abc import Sequence
from typing import Any, Protocol

from worker.core.types import ToolSpec


class ToolClient(Protocol):
    """Runs tools. Adapters: fake (tests), direct API calls (M1), MCP gateway (M4)."""

    def describe(self, names: Sequence[str]) -> list[ToolSpec]:
        """Schemas for these tools, in the same order. Raises ToolError for an unknown name."""
        ...

    def call(self, name: str, arguments: dict[str, Any]) -> Any:
        """Runs one tool and returns a JSON-compatible result. Raises ToolError on failure."""
        ...
