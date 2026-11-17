"""Behavior every ToolClient adapter must have. The MCP adapter (M4) joins ADAPTERS."""

import pytest
from fake_github_server import FakeGitHubServer, fixture

from worker.adapters.tools_fake import issue_triage_tools
from worker.adapters.tools_local import GitHubTools
from worker.core.errors import ToolError


def _github():
    repo = "acme/payments"
    with FakeGitHubServer() as server:
        server.on("GET", f"/repos/{repo}/labels/bug", fixture("label"))
        server.on("POST", f"/repos/{repo}/issues/42/labels", fixture("add_labels"))
        yield GitHubTools("test-token", [repo], api_url=server.url)


ADAPTERS = {"fake": lambda: iter([issue_triage_tools()]), "local_github": _github}


@pytest.fixture(params=ADAPTERS.values(), ids=ADAPTERS.keys())
def tools(request):
    yield from request.param()


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
