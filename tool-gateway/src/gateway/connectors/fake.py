"""Fake connector (`CONNECTORS=fake`): the tools of contracts/examples/agent-spec.valid.yaml with
canned results, the same names, schemas, flags and results as the worker's
`tools_fake.issue_triage_tools()`. For tests, `make demo` and chaos runs; it calls nothing.
Calls are recorded in `calls` (deterministic, shared test infrastructure)."""

import threading
from typing import Any

from gateway.tools.registry import CallContext, Tool


def _schema(required: dict[str, str], optional: dict[str, str] | None = None) -> dict[str, Any]:
    props = {name: {"type": kind} for name, kind in {**required, **(optional or {})}.items()}
    return {
        "type": "object",
        "properties": props,
        "required": list(required),
        "additionalProperties": False,
    }


class FakeConnector:
    name = "fake"

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any], str]] = []  # (tool, arguments, run id)
        self._lock = threading.Lock()

    def _recorded(self, name: str, result: Any):
        def run(arguments: dict[str, Any], context: CallContext) -> Any:
            with self._lock:
                self.calls.append((name, dict(arguments), context.claims.run_id))
            return result

        return run

    def tools(self) -> list[Tool]:
        return [
            Tool(
                "github.search_issues",
                "Search issues in a repository",
                _schema({"repo": "string", "query": "string"}),
                self._recorded(
                    "github.search_issues",
                    {"items": [{"number": 7, "title": "Refunds fail for EUR cards"}]},
                ),
                read_only=True,
                idempotent=True,
            ),
            Tool(
                "github.add_label",
                "Add a label to an issue",
                _schema({"repo": "string", "issue_number": "integer", "label": "string"}),
                self._recorded("github.add_label", {"ok": True}),
                idempotent=True,
            ),
            Tool(
                "github.close_issue",
                "Close an issue",
                _schema({"repo": "string", "issue_number": "integer"}, {"reason": "string"}),
                self._recorded("github.close_issue", {"ok": True}),
                idempotent=True,
                destructive=True,
            ),
            Tool(
                "slack.post_message",
                "Post a message to a Slack channel",
                _schema({"channel": "string", "text": "string"}),
                self._recorded("slack.post_message", {"ok": True, "ts": "1731800000.000100"}),
            ),
        ]
