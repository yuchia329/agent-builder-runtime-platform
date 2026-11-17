"""Fake tools that record every call. No network; used by tests and, later, the chaos test,
which counts duplicate side effects from this log."""

from __future__ import annotations

import copy
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from worker.core.errors import ToolError
from worker.core.types import ToolSpec

Handler = Callable[[dict[str, Any]], Any]


@dataclass(frozen=True)
class FakeTool:
    spec: ToolSpec
    handler: Handler


class FakeTools:
    def __init__(self, tools: Sequence[FakeTool]) -> None:
        self._tools = {t.spec.name: t for t in tools}
        self._lock = threading.Lock()
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def describe(self, names: Sequence[str]) -> list[ToolSpec]:
        missing = [n for n in names if n not in self._tools]
        if missing:
            raise ToolError(f"Unknown tools: {', '.join(missing)}")
        return [self._tools[n].spec for n in names]

    def call(self, name: str, arguments: dict[str, Any]) -> Any:
        tool = self._tools.get(name)
        if tool is None:
            raise ToolError(f"Unknown tool: {name}")
        with self._lock:
            self.calls.append((name, copy.deepcopy(arguments)))
        return tool.handler(arguments)


def _schema(required: dict[str, str], optional: dict[str, str] | None = None) -> dict[str, Any]:
    props = {k: {"type": v} for k, v in {**required, **(optional or {})}.items()}
    return {
        "type": "object",
        "properties": props,
        "required": list(required),
        "additionalProperties": False,
    }


def issue_triage_tools() -> FakeTools:
    """Fakes for the tools in contracts/examples/agent-spec.valid.yaml, with canned results."""
    return FakeTools(
        [
            FakeTool(
                ToolSpec(
                    "github.search_issues",
                    "Search issues in a repository",
                    _schema({"repo": "string", "query": "string"}),
                ),
                lambda a: {"items": [{"number": 7, "title": "Refunds fail for EUR cards"}]},
            ),
            FakeTool(
                ToolSpec(
                    "github.add_label",
                    "Add a label to an issue",
                    _schema({"repo": "string", "issue_number": "integer", "label": "string"}),
                ),
                lambda a: {"ok": True},
            ),
            FakeTool(
                ToolSpec(
                    "github.close_issue",
                    "Close an issue",
                    _schema({"repo": "string", "issue_number": "integer"}, {"reason": "string"}),
                ),
                lambda a: {"ok": True},
            ),
            FakeTool(
                ToolSpec(
                    "slack.post_message",
                    "Post a message to a Slack channel",
                    _schema({"channel": "string", "text": "string"}),
                ),
                lambda a: {"ok": True, "ts": "1731800000.000100"},
            ),
        ]
    )
