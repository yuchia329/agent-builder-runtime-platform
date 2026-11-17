"""OpenAICompatModel: the request it sends and how it reads replies (recorded MiniMax fixtures)."""

import copy

import pytest
from fake_openai_server import FakeOpenAIServer, fixture

from worker.adapters.model_openai_compat import OpenAICompatModel
from worker.core.errors import ModelError
from worker.core.types import (
    Message,
    ModelTurn,
    StopReason,
    ToolCall,
    ToolResult,
    ToolSpec,
    Usage,
)

TOOL = ToolSpec("github.add_label", "Add a label", {"type": "object"})
KEY = "sk-test-secret"


@pytest.fixture
def server():
    with FakeOpenAIServer() as s:
        yield s


def model(server, **kwargs):
    kwargs.setdefault("sleep", lambda _: None)
    return OpenAICompatModel(server.base_url, KEY, **kwargs)


def reply(finish_reason="stop", usage=True, **message):
    body = {
        "choices": [{"finish_reason": finish_reason, "message": {"role": "assistant", **message}}]
    }
    if usage:
        body["usage"] = {"prompt_tokens": 10, "completion_tokens": 5}
    return body


def test_request_shape(server):
    server.reply(reply(content="ok"))
    model(server, max_tokens=512, extra_body={"reasoning_split": True}).complete(
        "MiniMax-M3", "Be brief.", [Message.user("hi")], [TOOL]
    )

    body, headers = server.requests[0]
    assert headers["Authorization"] == f"Bearer {KEY}"
    assert body["model"] == "MiniMax-M3"
    assert body["max_completion_tokens"] == 512
    assert body["reasoning_split"] is True
    assert body["messages"] == [
        {"role": "system", "content": "Be brief."},
        {"role": "user", "content": "hi"},
    ]
    assert body["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "github-add_label",
                "description": "Add a label",
                "parameters": {"type": "object"},
            },
        }
    ]


def test_no_api_key_sends_no_authorization(server):
    server.reply(reply(content="ok"))
    OpenAICompatModel(server.base_url).complete("llama3.1", "s", [Message.user("hi")], [])

    body, headers = server.requests[0]
    assert "Authorization" not in headers
    assert "tools" not in body


def test_history_is_sent_back_with_reasoning_and_tool_results(server):
    server.reply(reply(content="ok"))
    turn = ModelTurn(
        StopReason.TOOL_USE,
        tool_calls=(ToolCall("call_1", "github.add_label", {"label": "bug"}),),
        provider_data={"reasoning_content": "Crash means bug."},
    )
    history = [
        Message.user("Issue 42: crash"),
        Message.assistant(turn),
        Message.tool(ToolResult("call_1", {"labels": ["bug"]})),
        Message.tool(ToolResult("call_2", "repo not found", is_error=True)),
    ]

    model(server).complete("m", "s", history, [TOOL])

    sent = server.bodies[0]["messages"]
    assert sent[2] == {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "github-add_label", "arguments": '{"label": "bug"}'},
            }
        ],
        "reasoning_content": "Crash means bug.",
    }
    assert sent[3] == {"role": "tool", "tool_call_id": "call_1", "content": '{"labels": ["bug"]}'}
    assert sent[4]["content"] == "Error: repo not found"


def test_reads_recorded_minimax_tool_turn(server):
    server.reply(fixture("minimax", "tool_turn"))

    turn = model(server).complete("MiniMax-M3", "s", [Message.user("hi")], [TOOL])

    assert turn.stop_reason is StopReason.TOOL_USE
    assert [(c.name, c.arguments) for c in turn.tool_calls] == [
        ("github.add_label", {"label": "bug"})
    ]
    assert turn.usage == Usage(425, 148)
    assert "bug" in turn.provider_data["reasoning_content"]


def test_reads_recorded_minimax_final_turn(server):
    server.reply(fixture("minimax", "final_turn"))

    turn = model(server).complete("MiniMax-M3", "s", [Message.user("hi")], [TOOL])

    assert turn.stop_reason is StopReason.END_TURN
    assert "bug" in turn.text
    assert turn.provider_data is None


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (reply("length", content="cut"), StopReason.MAX_TOKENS),
        (reply("content_filter", content=""), StopReason.REFUSAL),
        (reply("stop", content="", refusal="I can't help with that."), StopReason.REFUSAL),
        (
            reply(
                "stop",
                tool_calls=[
                    {"id": "c", "function": {"name": "github-add_label", "arguments": "{}"}}
                ],
            ),
            StopReason.TOOL_USE,
        ),
    ],
    ids=["length", "content_filter", "refusal_field", "tool_calls_with_stop"],
)
def test_stop_reasons(server, body, expected):
    server.reply(body)
    assert model(server).complete("m", "s", [Message.user("hi")], []).stop_reason is expected


def test_http_error_is_model_error_without_the_key(server):
    server.reply({"error": {"message": "invalid api key"}}, status=401)

    with pytest.raises(ModelError, match="HTTP 401") as e:
        model(server).complete("m", "s", [Message.user("hi")], [])
    assert KEY not in str(e.value)


def test_minimax_error_inside_http_200(server):
    body = copy.deepcopy(fixture("minimax", "final_turn"))
    body["base_resp"] = {"status_code": 1008, "status_msg": "insufficient balance"}
    server.reply(body)

    with pytest.raises(ModelError, match="1008: insufficient balance"):
        model(server).complete("m", "s", [Message.user("hi")], [])


@pytest.mark.parametrize(
    "body",
    [
        reply(content="ok", usage=False),
        reply(
            "tool_calls",
            tool_calls=[{"id": "c", "function": {"name": "x-y", "arguments": "{oops"}}],
        ),
        reply(
            "tool_calls", tool_calls=[{"id": "c", "function": {"name": "x-y", "arguments": "[1]"}}]
        ),
        {"unexpected": True},
        "not json",
    ],
    ids=["no_usage", "arguments_not_json", "arguments_not_object", "no_choices", "body_not_json"],
)
def test_unusable_reply_is_model_error(server, body):
    server.reply(body)
    with pytest.raises(ModelError):
        model(server).complete("m", "s", [Message.user("hi")], [])


def test_unreachable_provider_is_model_error():
    unreachable = OpenAICompatModel(
        "http://127.0.0.1:9/v1", KEY, timeout_seconds=2, sleep=lambda _: None
    )
    with pytest.raises(ModelError, match="Model request failed") as e:
        unreachable.complete("m", "s", [Message.user("hi")], [])
    assert KEY not in str(e.value)


# --- retries -------------------------------------------------------------------------------


def recording_model(server, sleeps, **kwargs):
    return OpenAICompatModel(server.base_url, KEY, sleep=sleeps.append, **kwargs)


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_retries_throttling_and_server_errors(server, status):
    sleeps = []
    server.reply({"error": "busy"}, status=status).reply(fixture("minimax", "final_turn"))

    turn = recording_model(server, sleeps).complete("m", "s", [Message.user("hi")], [])

    assert turn.stop_reason is StopReason.END_TURN
    assert len(server.requests) == 2
    assert len(sleeps) == 1 and 0.75 <= sleeps[0] <= 1.25


def test_backoff_grows_then_gives_up(server):
    sleeps = []
    server.reply(*[{"error": "busy"}] * 4, status=503)

    with pytest.raises(ModelError, match="gave up after 4 attempts"):
        recording_model(server, sleeps).complete("m", "s", [Message.user("hi")], [])

    assert len(server.requests) == 4
    for slept, base in zip(sleeps, [1, 2, 4], strict=True):
        assert 0.75 * base <= slept <= 1.25 * base


def test_retry_after_is_honored_and_capped(server):
    sleeps = []
    server.reply({"error": "slow down"}, status=429, headers={"Retry-After": "7"})
    server.reply({"error": "slow down"}, status=429, headers={"Retry-After": "600"})
    server.reply(fixture("minimax", "final_turn"))

    recording_model(server, sleeps).complete("m", "s", [Message.user("hi")], [])

    assert sleeps == [7.0, 30.0]


def test_retries_minimax_rate_limit_inside_http_200(server):
    throttled = copy.deepcopy(fixture("minimax", "final_turn"))
    throttled["base_resp"] = {"status_code": 1002, "status_msg": "rate limit"}
    server.reply(throttled, fixture("minimax", "final_turn"))

    turn = model(server).complete("m", "s", [Message.user("hi")], [])

    assert turn.text and len(server.requests) == 2


def test_retries_network_errors_then_gives_up():
    sleeps = []
    unreachable = OpenAICompatModel(
        "http://127.0.0.1:9/v1", KEY, timeout_seconds=2, attempts=3, sleep=sleeps.append
    )
    with pytest.raises(ModelError, match="gave up after 3 attempts"):
        unreachable.complete("m", "s", [Message.user("hi")], [])
    assert len(sleeps) == 2


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_client_errors_are_not_retried(server, status):
    server.reply({"error": "no"}, status=status)

    with pytest.raises(ModelError, match=f"HTTP {status}"):
        model(server).complete("m", "s", [Message.user("hi")], [])

    assert len(server.requests) == 1


def test_minimax_balance_error_is_not_retried(server):
    body = copy.deepcopy(fixture("minimax", "final_turn"))
    body["base_resp"] = {"status_code": 1008, "status_msg": "insufficient balance"}
    server.reply(body)

    with pytest.raises(ModelError, match="1008"):
        model(server).complete("m", "s", [Message.user("hi")], [])

    assert len(server.requests) == 1
