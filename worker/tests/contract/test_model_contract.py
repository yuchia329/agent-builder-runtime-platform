"""Behavior every Model adapter must have: ask for a tool call, then answer once it has the
result. Real-provider adapters run against a local fake server replaying recorded replies."""

import pytest
from fake_bedrock import stubbed_bedrock
from fake_openai_server import FakeOpenAIServer, fixture

from worker.adapters.model_openai_compat import OpenAICompatModel
from worker.adapters.model_router import ModelRouter
from worker.adapters.model_stub import ScriptedModel, StubModel, final_turn, tool_turn
from worker.core.types import Message, StopReason, ToolResult, ToolSpec

TOOL = ToolSpec(
    "github.add_label",
    "Add a label",
    {"type": "object", "properties": {"label": {"type": "string"}}, "required": ["label"]},
)

SCRIPT = [tool_turn(("github.add_label", {"label": "bug"})), final_turn("Done.")]


def _minimax():
    server = FakeOpenAIServer().reply(
        fixture("minimax", "tool_turn"), fixture("minimax", "final_turn")
    )
    with server:
        yield "MiniMax-M3", OpenAICompatModel(server.base_url, "test-key")


def _bedrock():
    yield "amazon.nova-lite-v1:0", stubbed_bedrock("tool_turn", "final_turn")


ADAPTERS = {
    "scripted": lambda: iter([("any", ScriptedModel(SCRIPT))]),
    "stub": lambda: iter([("any", StubModel(SCRIPT))]),
    "router": lambda: iter([("scripted/any", ModelRouter({"scripted": ScriptedModel(SCRIPT)}))]),
    "openai_compat_minimax": _minimax,
    "bedrock_nova_lite": _bedrock,
}


@pytest.fixture(params=ADAPTERS.values(), ids=ADAPTERS.keys())
def model(request):
    yield from request.param()


def test_tool_call_turn_then_final_answer(model):
    model_id, adapter = model
    conversation = [Message.user("Issue 42: crash")]

    first = adapter.complete(model_id, "Label the issue.", conversation, [TOOL])

    assert first.stop_reason is StopReason.TOOL_USE
    assert first.tool_calls and first.tool_calls[0].name == "github.add_label"
    assert first.tool_calls[0].id
    assert first.usage.input_tokens > 0

    conversation += [Message.assistant(first)] + [
        Message.tool(ToolResult(c.id, "Label added")) for c in first.tool_calls
    ]
    second = adapter.complete(model_id, "Label the issue.", conversation, [TOOL])

    assert second.stop_reason is StopReason.END_TURN
    assert second.tool_calls == ()
    assert second.text
    assert second.usage.output_tokens > 0
