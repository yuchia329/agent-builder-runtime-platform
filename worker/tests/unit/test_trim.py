"""fit_context: trims old tool results, then old exchanges, never breaking call/result pairs."""

import pytest

from worker.core.trim import KEEP_RECENT, TRIM_NOTE, ContextTooLarge, fit_context, message_chars
from worker.core.types import Message, ModelTurn, StopReason, ToolCall, ToolResult


def chars(messages):
    """Estimate used by the tests: one token per character."""
    return sum(message_chars(m) for m in messages)


def exchange(n, result_size=1000, reasoning=None):
    call = ToolCall(f"call_{n}", "github.search_issues", {"query": f"q{n}"})
    turn = ModelTurn(
        StopReason.TOOL_USE,
        text=f"step {n}",
        tool_calls=(call,),
        provider_data={"reasoning_content": reasoning} if reasoning else None,
    )
    return [Message.assistant(turn), Message.tool(ToolResult(call.id, "x" * result_size))]


def conversation(exchanges=5, **kwargs):
    messages = [Message.user("Run input: issue 42")]
    for n in range(exchanges):
        messages += exchange(n, **kwargs)
    return messages


def assert_well_formed(messages):
    assert messages[0].role == "user"
    assert messages[1].role == "assistant"
    open_calls = set()
    for m in messages[1:]:
        if m.role == "assistant":
            assert not open_calls, "tool calls left without results"
            open_calls = {c.id for c in m.tool_calls}
        else:
            assert m.tool_result.call_id in open_calls, "tool result without its call"
            open_calls.discard(m.tool_result.call_id)


def test_fitting_conversation_is_unchanged():
    messages = conversation()
    assert fit_context(messages, chars(messages), chars) == messages


def test_stage_1_shortens_oldest_tool_results_first():
    messages = conversation(5)

    trimmed = fit_context(messages, chars(messages) - 500, chars)

    assert len(trimmed) == len(messages)
    assert "removed to save context" in trimmed[2].tool_result.content
    assert trimmed[4].tool_result.content == "x" * 1000  # second exchange not needed
    assert_well_formed(trimmed)


def test_stage_1_never_touches_recent_exchanges():
    messages = conversation(5, reasoning="thinking " * 50)
    limit = chars(messages) - 3 * 1000  # needs the three oldest exchanges shortened

    trimmed = fit_context(messages, limit, chars)

    old, recent = trimmed[1:7], trimmed[-2 * KEEP_RECENT :]
    assert all(m.provider_data is None for m in old if m.role == "assistant")
    assert recent == messages[-2 * KEEP_RECENT :]


def test_stage_2_drops_oldest_exchanges_and_notes_it():
    messages = conversation(5)

    trimmed = fit_context(messages, 1500, chars)

    assert trimmed[0].text.endswith(TRIM_NOTE)
    assert trimmed[-2:] == messages[-2:]  # latest exchange kept whole
    assert len(trimmed) < len(messages)
    assert_well_formed(trimmed)


def test_note_is_added_once_across_calls():
    trimmed = fit_context(conversation(5), 1500, chars)
    trimmed += exchange(9)

    again = fit_context(trimmed, 1500, chars)

    assert again[0].text.count(TRIM_NOTE.strip()) == 1


def test_too_large_even_after_trimming():
    with pytest.raises(ContextTooLarge, match="does not fit"):
        fit_context([Message.user("x" * 5000)], 100, chars)
