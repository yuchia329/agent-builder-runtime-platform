"""GatewayTools against the real tool gateway with its fake connector (tests/live_gateway.py)."""

import pytest

from worker.adapters.tools_mcp import GatewayTools, _structured
from worker.core.errors import ToolError
from worker.core.types import ToolContext

SEARCH = {"repo": "acme/payments", "query": "refund"}
KEY = "b" * 64


def context(token, step=3):
    return ToolContext("run_mcp", token, step, KEY if step else None)


@pytest.fixture
def tools(live_gateway):
    return GatewayTools(live_gateway.url)


@pytest.fixture
def token(run_tokens):
    return run_tokens.sign("run_mcp")


def test_annotations_become_tool_flags(tools, token):
    search, label, close = tools.describe(
        ["github.search_issues", "github.add_label", "github.close_issue"], context(token, None)
    )

    assert (search.read_only, search.idempotent) == (True, True)
    assert (label.read_only, label.idempotent) == (False, True)
    assert (close.read_only, close.idempotent) == (False, True)
    assert search.input_schema["required"] == ["repo", "query"]


def test_describe_only_offers_the_tokens_tools(tools, run_tokens):
    token = run_tokens.sign("run_mcp", tools=["github.search_issues"])

    with pytest.raises(ToolError, match="Unknown tools: github.add_label"):
        tools.describe(["github.search_issues", "github.add_label"], context(token, None))


def test_call_returns_the_structured_result_and_sends_meta(tools, token):
    # The gateway refuses calls without the agentplatform/ _meta keys, so a result proves them.
    result = tools.call("github.search_issues", SEARCH, context(token))

    assert result == {"items": [{"number": 7, "title": "Refunds fail for EUR cards"}]}


def test_tool_error_reaches_the_model_as_tool_error(tools, token):
    with pytest.raises(ToolError, match="Invalid arguments"):
        tools.call("github.search_issues", {"repo": 1}, context(token))


def test_approval_tool_refusal_is_a_tool_error(tools, run_tokens):
    token = run_tokens.sign("run_mcp", require_approval=["github.close_issue"])

    with pytest.raises(ToolError, match="needs a person's approval"):
        tools.call(
            "github.close_issue", {"repo": "acme/payments", "issue_number": 8}, context(token)
        )


def test_tool_outside_the_token_is_refused(tools, run_tokens):
    token = run_tokens.sign("run_mcp", tools=["github.search_issues"])

    with pytest.raises(ToolError, match="refused the request"):
        tools.call(
            "github.add_label", {"repo": "r", "issue_number": 1, "label": "bug"}, context(token)
        )


@pytest.mark.parametrize("bad", ["forged", "expired"])
def test_refused_token_is_a_tool_error_without_the_token(tools, run_tokens, bad):
    from live_gateway import RunTokens

    if bad == "forged":
        token = RunTokens().sign("run_mcp")  # another key
    else:
        token = run_tokens.sign("run_mcp", iat=1_700_000_000, exp=1_700_000_300)

    with pytest.raises(ToolError, match="HTTP 401") as e:
        tools.call("github.search_issues", SEARCH, context(token))
    assert token not in str(e.value)


def test_run_without_token_fails_before_any_request():
    tools = GatewayTools("http://127.0.0.1:9")  # nothing listens; never contacted

    with pytest.raises(ToolError, match="no run token"):
        tools.describe(["github.search_issues"], ToolContext("run_mcp", None))


def test_unreachable_gateway_is_a_tool_error(token):
    with pytest.raises(ToolError, match="Tool gateway unavailable"):
        GatewayTools("http://127.0.0.1:9").call("github.search_issues", SEARCH, context(token))


def test_call_without_step_is_refused_locally(tools, token):
    with pytest.raises(ToolError, match="without a step"):
        tools.call("github.search_issues", SEARCH, context(token, None))


@pytest.mark.parametrize(
    ("content", "expected"),
    [({"result": [1, 2]}, [1, 2]), ({"result": "ok", "x": 1}, {"result": "ok", "x": 1}), ({}, {})],
)
def test_non_object_results_are_unwrapped(content, expected):
    assert _structured("t", content) == expected


def test_missing_structured_result_is_a_tool_error():
    with pytest.raises(ToolError, match="no structured result"):
        _structured("t", None)
