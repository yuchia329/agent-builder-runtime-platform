"""Spans of the run's trace (ADR 0022): /mcp continues the worker's `traceparent`, each tool call
and connector attempt gets a span, the control plane receives the trace, GitHub never does, and no
argument value, URL or exception message reaches a span."""

import json
import logging

import pytest
from fake_control_plane import FakeControlPlane
from fake_github import REPO, FakeGitHub, fixture
from gateway_testing import LiveGateway, claims, mcp_client, meta, token
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode
from test_approvals import ARGS, CLAIMS, STEP, TOOL, approval, run
from test_github_connector import client as github_client

from gateway.adapters.control_plane_http import HttpControlPlane
from gateway.approvals import ApprovalCheck
from gateway.auth.run_token import RunClaims
from gateway.config import Settings
from gateway.tools.registry import CallContext
from gateway.tracing import build_tracing, trace_id

TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"
WORKER_SPAN = "00f067aa0ba902b7"
TRACEPARENT = f"00-{TRACE_ID}-{WORKER_SPAN}-01"
SEARCH = {"repo": "octocat/Hello-World", "query": "refund EUR secret-looking-text"}


@pytest.fixture
def spans():
    return InMemorySpanExporter()


@pytest.fixture
def tracing(spans):
    return build_tracing("none", span_exporter=spans)


@pytest.fixture
def traced_gateway(keys, fake, tracing):
    with LiveGateway(keys, fake, tracing=tracing) as live:
        yield live


def _named(spans, name):
    return [s for s in spans.get_finished_spans() if s.name == name]


def _hex(span_id):
    return format(span_id, "016x")


@pytest.mark.anyio
async def test_mcp_spans_continue_the_workers_trace(traced_gateway, keys, spans):
    async with mcp_client(traced_gateway, token(keys), traceparent=TRACEPARENT) as client:
        await client.list_tools()
        result = await client.call_tool("github.search_issues", SEARCH, meta=meta(step=3))

    assert not result.is_error
    listings = _named(spans, "mcp.tools_list")  # the SDK client may list again before a call
    [call] = _named(spans, "tool.execute")
    listing = listings[0]
    for span in (*listings, call):
        assert format(span.context.trace_id, "032x") == TRACE_ID
        assert _hex(span.parent.span_id) == WORKER_SPAN
        assert span.attributes["run.id"] == "run_test"
        assert span.attributes["tenant.id"] == "tenant-test"
        assert span.attributes["agent.name"] == "issue-triage"
        assert span.attributes["agent.version"] == 4
    assert listing.attributes["tools.count"] == 2
    assert call.attributes["gen_ai.tool.name"] == "github.search_issues"
    assert call.attributes["run.step"] == 3
    assert call.attributes["tool.outcome"] == "ok"


@pytest.mark.anyio
async def test_access_log_line_names_the_trace(traced_gateway, keys, caplog):
    with caplog.at_level(logging.INFO, logger="gateway.access"):
        async with mcp_client(traced_gateway, token(keys), traceparent=TRACEPARENT) as client:
            await client.call_tool("github.search_issues", SEARCH, meta=meta())

    (line,) = [json.loads(r.getMessage()) for r in caplog.records if r.name == "gateway.access"]
    assert line["trace_id"] == TRACE_ID


@pytest.mark.anyio
async def test_refused_and_failed_calls_carry_their_outcome(traced_gateway, keys, spans):
    async with mcp_client(traced_gateway, token(keys), traceparent=TRACEPARENT) as client:
        bad = await client.call_tool("github.search_issues", {"repo": 1}, meta=meta())

    assert bad.is_error
    [call] = _named(spans, "tool.execute")
    assert call.attributes["tool.outcome"] == "error"


@pytest.mark.anyio
@pytest.mark.parametrize("header", [None, "not-a-traceparent", "00-" + "0" * 32 + "-" + "0" * 16])
async def test_without_a_valid_traceparent_the_call_starts_its_own_trace(
    traced_gateway, keys, spans, header
):
    async with mcp_client(traced_gateway, token(keys), traceparent=header) as client:
        result = await client.call_tool("github.search_issues", SEARCH, meta=meta())

    assert not result.is_error
    [call] = _named(spans, "tool.execute")
    assert call.parent is None
    assert call.context.trace_id != int(TRACE_ID, 16)


@pytest.mark.anyio
async def test_no_argument_value_reaches_a_span(traced_gateway, keys, spans):
    async with mcp_client(traced_gateway, token(keys), traceparent=TRACEPARENT) as client:
        await client.call_tool("github.search_issues", SEARCH, meta=meta())

    values = [str(v) for s in spans.get_finished_spans() for v in s.attributes.values()]
    for argument in SEARCH.values():
        assert not any(argument in v for v in values)


@pytest.mark.anyio
async def test_approval_check_sends_the_trace_to_the_control_plane(keys, fake, tracing, spans):
    with FakeControlPlane() as plane:
        plane.put(run(approval=approval()))
        approvals = ApprovalCheck(HttpControlPlane(plane.url, sleep=lambda _: None))
        with LiveGateway(keys, fake, approvals=approvals, tracing=tracing) as live:
            async with mcp_client(live, token(keys, CLAIMS), traceparent=TRACEPARENT) as client:
                result = await client.call_tool(TOOL, ARGS, meta=meta(step=STEP))

    assert not result.is_error
    [check] = _named(spans, "approval.check")
    [call] = _named(spans, "tool.execute")
    assert check.parent.span_id == call.context.span_id
    assert check.attributes["approval.outcome"] == "approved"
    [(_, headers)] = plane.requests
    sent = {k.lower(): v for k, v in headers.items()}["traceparent"]
    assert sent.startswith(f"00-{TRACE_ID}-")  # the run's trace, from inside the check


def test_connector_attempts_are_spans_without_urls_and_without_traceparent(tracing, spans):
    with FakeGitHub() as github:
        github.on("GET", "/search/issues", fixture("search_issues"))
        github.fail_next(502)
        connector = github_client(github)
        connector._tracing = tracing
        context = CallContext(RunClaims.from_payload(claims()), 3, "a" * 64)
        with tracing.span("tool.execute", traceparent=TRACEPARENT):
            connector.call(connector.connector.tools["github.search_issues"], {"q": "x"}, context)

    attempts = sorted(_named(spans, "connector.request"), key=lambda s: s.start_time)
    assert [s.attributes["connector.attempt"] for s in attempts] == [1, 2]
    assert [s.attributes["http.response.status_code"] for s in attempts] == [502, 200]
    for span in attempts:
        assert span.attributes["connector.name"] == "github"
        assert span.attributes["connector.operation"] == "search/issues-and-pull-requests"
        assert format(span.context.trace_id, "032x") == TRACE_ID
        assert not any(REPO in str(v) or "http" in str(v) for v in span.attributes.values())
    for request in github.requests:
        assert "traceparent" not in {k.lower() for k in request.headers}


def test_a_failure_records_the_exception_type_never_its_message(tracing, spans):
    with pytest.raises(ValueError), tracing.span("tool.execute"):
        raise ValueError("token=secret-value")

    [span] = spans.get_finished_spans()
    assert span.status.status_code is StatusCode.ERROR
    assert span.status.description == "ValueError"
    assert span.events == ()


def test_trace_id_is_none_outside_a_span(tracing):
    assert trace_id() is None
    with tracing.span("tool.execute", traceparent=TRACEPARENT):
        assert trace_id() == TRACE_ID


def test_tracing_exporter_setting():
    base = {"RUN_TOKEN_PUBLIC_KEY": "key"}
    assert Settings.from_env(base).tracing_exporter == "none"
    assert Settings.from_env({**base, "TRACING_EXPORTER": "logging"}).tracing_exporter == "logging"
    with pytest.raises(ValueError, match="TRACING_EXPORTER"):
        Settings.from_env({**base, "TRACING_EXPORTER": "xray"})
    with pytest.raises(ValueError, match="unknown exporter"):
        build_tracing("xray")
