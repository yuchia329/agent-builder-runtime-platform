"""A run's spans (ADR 0022): the poll loop continues the trace from the queue message, handle_run
adds a span per model call, tool call, checkpoint and control-plane call, and nothing from the
run's input, output or tool arguments reaches a span."""

import json

from contracts import example_run
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from worker.adapters.clock_system import SystemClock
from worker.adapters.control_plane_memory import InMemoryControlPlane
from worker.adapters.model_stub import StubModel, demo_turns
from worker.adapters.queue_memory import InMemoryRunQueue
from worker.adapters.run_state_memory import InMemoryRunStateStore
from worker.adapters.tools_fake import issue_triage_tools
from worker.adapters.tracing_otel import build_tracer
from worker.core.handle_run import Deps
from worker.entrypoints.poll_loop import PollLoop

RUN = example_run()
RUN_ID = RUN["run_id"]
TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"
TRACE_PARENT = f"00-{TRACE_ID}-00f067aa0ba902b7-01"


def _run_traced(trace_parent=TRACE_PARENT, *, control_plane=None, model=None, ends="SUCCEEDED"):
    spans = InMemorySpanExporter()
    tracer = build_tracer("none", "us-east-1", span_exporter=spans)
    control_plane = control_plane or InMemoryControlPlane()
    control_plane.add_run(example_run())
    clock = SystemClock()
    state = InMemoryRunStateStore(clock)
    deps = Deps(
        control_plane,
        model or StubModel(demo_turns()),
        issue_triage_tools(),
        clock,
        state=state,
        tracer=tracer,
    )
    queue = InMemoryRunQueue()
    body = {"schema_version": 1, "run_id": RUN_ID, "tenant_id": RUN["tenant_id"]}
    queue.send(json.dumps(body), trace_parent)
    loop = PollLoop(queue, deps, state, wait_seconds=0)
    assert loop.poll_once() == 1
    loop.drain()
    assert control_plane.run_json(RUN_ID)["status"] == ends
    return {s.context.span_id: s for s in spans.get_finished_spans()}, deps


def _named(spans, name):
    return [s for s in spans.values() if s.name == name]


def test_every_span_of_the_attempt_is_in_the_runs_trace():
    spans, _ = _run_traced()

    [attempt] = _named(spans, "run.attempt")
    assert {format(s.context.trace_id, "032x") for s in spans.values()} == {TRACE_ID}
    assert format(attempt.parent.span_id, "016x") == "00f067aa0ba902b7"
    for span in spans.values():
        if span is not attempt:
            assert span.parent.span_id == attempt.context.span_id
    assert attempt.attributes["run.id"] == RUN_ID
    assert attempt.attributes["tenant.id"] == RUN["tenant_id"]
    assert attempt.attributes["agent.name"] == RUN["agent_name"]
    assert attempt.attributes["agent.version"] == RUN["agent_version"]
    assert attempt.attributes["run.status"] == "SUCCEEDED"


def test_model_tool_and_checkpoint_spans_carry_counts_and_names():
    spans, _ = _run_traced()

    models = sorted(_named(spans, "model.call"), key=lambda s: s.attributes["run.step"])
    tools = sorted(_named(spans, "tool.call"), key=lambda s: s.attributes["run.step"])
    checkpoints = _named(spans, "checkpoint")
    assert [s.attributes["run.step"] for s in models] == [1, 3, 5]
    assert [s.attributes["gen_ai.tool.name"] for s in tools] == [
        "github.search_issues",
        "github.add_label",
    ]
    assert [s.attributes["tool.outcome"] for s in tools] == ["ok", "ok"]
    assert len(checkpoints) == 5  # one per step
    first = models[0].attributes
    assert first["gen_ai.request.model"] == RUN["spec"]["model"]
    assert first["gen_ai.usage.input_tokens"] >= 0
    assert "cost.usd" in first
    assert models[-1].attributes["stop_reason"] == "end_turn"


def test_no_input_output_or_arguments_reach_a_span():
    spans, deps = _run_traced()

    texts = [json.dumps(v) for v in RUN["input"].values()]
    texts += [json.dumps(args) for _, args in deps.tools.calls]
    texts.append(deps.control_plane.run_json(RUN_ID)["output"])
    values = [str(v) for s in spans.values() for v in s.attributes.values()]
    for text in texts:
        assert not any(text in v or v in text and len(v) > 20 for v in values), text


def test_a_message_without_trace_context_starts_its_own_trace():
    spans, _ = _run_traced(trace_parent=None)

    [attempt] = _named(spans, "run.attempt")
    assert attempt.parent is None
    assert len({s.context.trace_id for s in spans.values()}) == 1


def _control_plane_calls(spans):
    """(name, status, outcome) of the control-plane spans in time order, after checking each is
    a child of the attempt."""
    [attempt] = _named(spans, "run.attempt")
    calls = sorted(
        (s for s in spans.values() if s.name.startswith("control_plane.")),
        key=lambda s: s.start_time,
    )
    for call in calls:
        assert call.parent.span_id == attempt.context.span_id
    return [(c.name, c.attributes.get("run.status"), c.attributes["outcome"]) for c in calls]


def test_control_plane_calls_have_spans_with_the_status_and_outcome():
    spans, _ = _run_traced()

    assert _control_plane_calls(spans) == [
        ("control_plane.update", "RUNNING", "ok"),
        ("control_plane.update", "SUCCEEDED", "ok"),
    ]


def test_a_start_put_off_at_the_cap_is_a_deferred_update():
    spans, _ = _run_traced(control_plane=InMemoryControlPlane(max_running=0), ends="QUEUED")

    assert _control_plane_calls(spans) == [("control_plane.update", "RUNNING", "deferred")]


def test_a_start_over_the_spend_cap_is_a_conflict():
    control_plane = InMemoryControlPlane()
    control_plane.over_budget.add(RUN["tenant_id"])

    spans, _ = _run_traced(control_plane=control_plane, ends="FAILED")

    assert _control_plane_calls(spans) == [("control_plane.update", "RUNNING", "conflict")]


def test_a_run_set_aside_reports_progress_then_defers():
    model = StubModel(demo_turns(), rate_limited_share=1.0)

    spans, _ = _run_traced(model=model, ends="QUEUED")

    assert _control_plane_calls(spans) == [
        ("control_plane.update", "RUNNING", "ok"),
        ("control_plane.update", "RUNNING", "ok"),
        ("control_plane.defer", "QUEUED", "ok"),
    ]
