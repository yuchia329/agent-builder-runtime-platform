"""BedrockConverseModel against a stubbed client replaying recorded Nova Lite replies."""

import copy

import pytest
from fake_bedrock import fixture, stub_client, stubbed_bedrock

from worker.adapters.model_bedrock import BedrockConverseModel
from worker.core.errors import ModelError, ModelRateLimited
from worker.core.types import Message, ModelTurn, StopReason, ToolCall, ToolResult, ToolSpec, Usage

MODEL = "amazon.nova-lite-v1:0"
TOOLS = [
    ToolSpec("github.add_label", "Add a label", {"type": "object", "properties": {}}),
    ToolSpec("github.search_issues", "Search", {"type": "object", "properties": {}}),
]


def recording(*replies, **kwargs):
    """Adapter plus the list of requests it sent."""
    client, stubber = stub_client()
    for reply in replies:
        stubber.add_response("converse", fixture(reply) if isinstance(reply, str) else reply)
    sent = []
    original = client.converse

    def converse(**request):
        sent.append(copy.deepcopy(request))
        return original(**request)

    client.converse = converse
    return BedrockConverseModel(client=client, **kwargs), sent


def reply(content, stop="end_turn", usage=(10, 5)):
    return {
        "output": {"message": {"role": "assistant", "content": content}},
        "stopReason": stop,
        "usage": {"inputTokens": usage[0], "outputTokens": usage[1], "totalTokens": sum(usage)},
        "metrics": {"latencyMs": 1},
    }


def test_tool_turn_maps_names_back_and_drops_nova_thinking():
    turn = stubbed_bedrock("tool_turn").complete(MODEL, "sys", [Message.user("hi")], TOOLS)

    assert turn.stop_reason is StopReason.TOOL_USE
    assert turn.tool_calls == (
        ToolCall("tooluse_2s5P59UhUb7Gxpf3y1ATgj", "github.add_label", {"label": "bug"}),
    )
    assert turn.text == ""
    assert turn.usage == Usage(421, 66)


def test_final_turn_unwraps_nova_response():
    turn = stubbed_bedrock("final_turn").complete(MODEL, "sys", [Message.user("hi")], TOOLS)

    assert turn.stop_reason is StopReason.END_TURN
    assert turn.text == "Done."


def test_request_shape():
    model, sent = recording(
        "final_turn",
        max_tokens=512,
        temperature=0,
        extra_body={"inferenceConfig": {"topK": 1}},
    )
    call = ToolCall("t1", "github.add_label", {"label": "bug"})
    conversation = [
        Message.user("Issue 4"),
        Message.assistant(ModelTurn(StopReason.TOOL_USE, text="", tool_calls=(call,))),
        Message.tool(ToolResult("t1", {"labels": ["bug"]})),
        Message.tool(ToolResult("t2", "no such label", is_error=True)),
    ]

    model.complete(MODEL, "Triage.", conversation, TOOLS)

    [request] = sent
    assert request["modelId"] == MODEL
    assert request["system"] == [{"text": "Triage."}]
    assert request["inferenceConfig"] == {"maxTokens": 512, "temperature": 0}
    assert request["additionalModelRequestFields"] == {"inferenceConfig": {"topK": 1}}
    assert [t["toolSpec"]["name"] for t in request["toolConfig"]["tools"]] == [
        "github_add_label",
        "github_search_issues",
    ]
    assert request["messages"] == [
        {"role": "user", "content": [{"text": "Issue 4"}]},
        {
            "role": "assistant",
            "content": [
                {
                    "toolUse": {
                        "toolUseId": "t1",
                        "name": "github_add_label",
                        "input": {"label": "bug"},
                    }
                }
            ],
        },
        {
            "role": "user",
            "content": [
                {"toolResult": {"toolUseId": "t1", "content": [{"text": '{"labels": ["bug"]}'}]}},
                {"toolResult": {"toolUseId": "t2", "content": [{"text": "Error: no such label"}]}},
            ],
        },
    ]


def test_temperature_zero_is_sent_and_none_is_not():
    model, sent = recording("final_turn", "final_turn", temperature=0)
    model.complete(MODEL, "s", [Message.user("x")], TOOLS)
    unset, sent_unset = recording("final_turn")
    unset.complete(MODEL, "s", [Message.user("x")], TOOLS)

    assert sent[0]["inferenceConfig"]["temperature"] == 0
    assert "temperature" not in sent_unset[0]["inferenceConfig"]
    assert "additionalModelRequestFields" not in sent_unset[0]


def test_reasoning_blocks_round_trip():
    thinking = {"reasoningContent": {"reasoningText": {"text": "hmm", "signature": "sig"}}}
    first = reply([thinking, {"text": "ok"}])
    model, sent = recording(first, "final_turn")

    turn = model.complete(MODEL, "s", [Message.user("x")], TOOLS)
    model.complete(
        MODEL, "s", [Message.user("x"), Message.assistant(turn), Message.user("y")], TOOLS
    )

    assert turn.provider_data == {"reasoning": [thinking]}
    assert sent[1]["messages"][1]["content"] == [thinking, {"text": "ok"}]


def test_unknown_tool_name_passes_through_for_the_loop_to_reject():
    use = {"toolUse": {"toolUseId": "t", "name": "shell_exec", "input": {}}}
    turn = stubbed_bedrock(reply([use], "tool_use")).complete(
        MODEL, "s", [Message.user("x")], TOOLS
    )

    assert turn.tool_calls[0].name == "shell_exec"


@pytest.mark.parametrize(
    ("stop", "expected"),
    [
        ("max_tokens", StopReason.MAX_TOKENS),
        ("guardrail_intervened", StopReason.REFUSAL),
        ("content_filtered", StopReason.REFUSAL),
        ("stop_sequence", StopReason.END_TURN),
    ],
)
def test_stop_reasons(stop, expected):
    turn = stubbed_bedrock(reply([{"text": "x"}], stop)).complete(
        MODEL, "s", [Message.user("x")], []
    )

    assert turn.stop_reason is expected


@pytest.mark.parametrize(
    ("stop", "match"),
    [
        ("malformed_tool_use", "malformed_tool_use"),
        ("model_context_window_exceeded", "context window"),
        ("something_new", "something_new"),
    ],
)
def test_bad_stop_reasons_are_errors(stop, match):
    with pytest.raises(ModelError, match=match):
        stubbed_bedrock(reply([{"text": "x"}], stop)).complete(MODEL, "s", [Message.user("x")], [])


def test_client_error_becomes_model_error():
    client, stubber = stub_client()
    stubber.add_client_error(
        "converse",
        service_error_code="ModelErrorException",
        service_message="Model produced invalid sequence as part of ToolUse.",
        http_status_code=424,
    )

    with pytest.raises(ModelError, match="ModelErrorException: Model produced invalid sequence"):
        BedrockConverseModel(client=client).complete(MODEL, "s", [Message.user("x")], TOOLS)


def test_missing_profile_is_a_model_error_at_call_time():
    model = BedrockConverseModel(profile="no-such-profile-for-tests", region="us-east-1")

    with pytest.raises(ModelError, match="ProfileNotFound"):
        model.complete(MODEL, "s", [Message.user("x")], TOOLS)


def test_uses_botocore_standard_retries():
    model = BedrockConverseModel(attempts=4)

    assert model._boto_config.retries == {"mode": "standard", "max_attempts": 4}


def test_throttling_after_botocore_retries_is_model_rate_limited():
    client, stubber = stub_client()
    stubber.add_client_error(
        "converse",
        service_error_code="ThrottlingException",
        service_message="Too many requests, please wait before trying again.",
        http_status_code=429,
    )

    with pytest.raises(ModelRateLimited, match="ThrottlingException"):
        BedrockConverseModel(client=client).complete(MODEL, "s", [Message.user("x")], TOOLS)
