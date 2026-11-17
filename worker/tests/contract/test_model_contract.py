"""Behavior every Model adapter must have. The Bedrock adapter (M1) joins ADAPTERS with a
recorded or live fixture that asks for a tool call and then answers."""

import pytest

from worker.adapters.model_stub import ScriptedModel, StubModel, final_turn, tool_turn
from worker.core.types import Message, StopReason, ToolSpec

TOOL = ToolSpec(
    "github.add_label",
    "Add a label",
    {"type": "object", "properties": {"label": {"type": "string"}}, "required": ["label"]},
)


SCRIPT = [tool_turn(("github.add_label", {"label": "bug"})), final_turn("Done.")]

ADAPTERS = {"scripted": lambda: ScriptedModel(SCRIPT), "stub": lambda: StubModel(SCRIPT)}


@pytest.fixture(params=ADAPTERS.values(), ids=ADAPTERS.keys())
def model(request):
    return request.param()


def test_tool_call_turn_then_final_answer(model):
    first = model.complete("Label the issue.", [Message.user("Issue 42: crash")], [TOOL])

    assert first.stop_reason is StopReason.TOOL_USE
    assert first.tool_calls and first.tool_calls[0].name == "github.add_label"
    assert first.tool_calls[0].id
    assert first.usage.input_tokens > 0

    second = model.complete(
        "Label the issue.", [Message.user("Issue 42: crash"), Message.assistant(first)], [TOOL]
    )
    assert second.stop_reason is StopReason.END_TURN
    assert second.tool_calls == ()
    assert second.text
