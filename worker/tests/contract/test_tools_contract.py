"""Behavior every ToolClient adapter must have. The MCP adapter runs against the real tool gateway
with its fake connector (tests/live_gateway.py)."""

import pytest
from fake_github_server import FakeGitHubServer, fixture

from worker.adapters.tools_fake import issue_triage_tools
from worker.adapters.tools_local import GitHubTools
from worker.adapters.tools_mcp import GatewayTools
from worker.core.errors import ToolError
from worker.core.types import ToolContext


class Bound:
    """An adapter with the run context core would pass."""

    def __init__(self, adapter, token=None):
        self._adapter, self._token = adapter, token

    def describe(self, names):
        return self._adapter.describe(names, ToolContext("run_contract", self._token))

    def call(self, name, arguments):
        context = ToolContext("run_contract", self._token, 2, "a" * 64)
        return self._adapter.call(name, arguments, context)


def _fake(request):
    yield Bound(issue_triage_tools())


def _github(request):
    repo = "acme/payments"
    with FakeGitHubServer() as server:
        server.on("GET", f"/repos/{repo}/labels/bug", fixture("label"))
        server.on("POST", f"/repos/{repo}/issues/42/labels", fixture("add_labels"))
        yield Bound(GitHubTools("test-token", [repo], api_url=server.url))


def _mcp(request):
    gateway = request.getfixturevalue("live_gateway")
    token = request.getfixturevalue("run_tokens").sign("run_contract")
    yield Bound(GatewayTools(gateway.url), token)


ADAPTERS = {"fake": _fake, "local_github": _github, "mcp_gateway": _mcp}


@pytest.fixture(params=ADAPTERS.values(), ids=ADAPTERS.keys())
def tools(request):
    yield from request.param(request)


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
