"""Behavior every ToolClient adapter must have. Direct (M1) and MCP (M4) adapters join ADAPTERS."""

import pytest

from worker.adapters.tools_fake import issue_triage_tools
from worker.core.errors import ToolError

ADAPTERS = {"fake": issue_triage_tools}


@pytest.fixture(params=ADAPTERS.values(), ids=ADAPTERS.keys())
def tools(request):
    return request.param()


def test_describe_returns_schemas_in_order(tools):
    specs = tools.describe(["github.add_label", "github.search_issues"])

    assert [s.name for s in specs] == ["github.add_label", "github.search_issues"]
    assert all(s.input_schema.get("type") == "object" for s in specs)
    assert all(s.description for s in specs)


def test_describe_unknown_tool_raises(tools):
    with pytest.raises(ToolError):
        tools.describe(["github.delete_repo"])


def test_call_returns_json_compatible_result(tools):
    result = tools.call(
        "github.add_label", {"repo": "acme/payments", "issue_number": 42, "label": "bug"}
    )

    assert isinstance(result, dict | list | str | int | float | bool)


def test_call_unknown_tool_raises(tools):
    with pytest.raises(ToolError):
        tools.call("github.delete_repo", {})
