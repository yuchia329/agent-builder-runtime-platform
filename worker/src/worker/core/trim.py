"""Keeps a run's conversation inside the model's context window (ADR 0009: we own the history).

The conversation is the run input (first user message) followed by exchanges: one assistant
turn plus the results of the tools it called. When the estimated size passes the limit:

1. Old tool results are replaced by a short placeholder and old reasoning (``provider_data``)
   is dropped, oldest first. Message order is unchanged, so every provider accepts it.
2. Then the oldest whole exchanges are dropped, and the run input gets a note saying so. The
   conversation still starts with the user message and alternates user/assistant turns, and a
   tool result never loses its call.
3. If the input plus the latest exchange still do not fit, ``ContextTooLarge``.

The last ``KEEP_RECENT`` exchanges are never shortened. No tokenizer here (core imports no
vendor code): the caller passes ``estimate``, a token count for a list of messages.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import replace

from worker.core.types import Message, ToolResult

KEEP_RECENT = 2
SHORT_RESULT = 200  # tool results this short are not worth replacing
TRIM_NOTE = (
    "\n\n[Note: earlier steps of this run were removed to fit the model's context window. "
    "Their tool calls already happened; do not repeat them unless needed.]"
)

Estimate = Callable[[Sequence[Message]], int]


class ContextTooLarge(Exception):
    """Even the run input plus the latest exchange do not fit the model's context window."""


def message_chars(message: Message) -> int:
    """Characters a message adds to a request; the basis for token estimates."""
    size = len(message.text)
    for call in message.tool_calls:
        size += len(call.id) + len(call.name) + len(json.dumps(call.arguments))
    if message.tool_result is not None:
        content = message.tool_result.content
        size += len(content if isinstance(content, str) else json.dumps(content))
    if message.provider_data:
        size += len(json.dumps(message.provider_data))
    return size


def fit_context(messages: Sequence[Message], limit: int, estimate: Estimate) -> list[Message]:
    """Returns the conversation unchanged if it fits ``limit`` tokens, else a trimmed copy."""
    if estimate(messages) <= limit:
        return list(messages)
    first, exchanges = _split(messages)

    # Stage 1: shorten old exchanges, oldest first.
    for i in range(max(0, len(exchanges) - KEEP_RECENT)):
        exchanges[i] = [_shorten(m) for m in exchanges[i]]
        if estimate(_join(first, exchanges)) <= limit:
            return _join(first, exchanges)

    # Stage 2: drop whole exchanges, oldest first, keeping at least the latest one.
    if len(exchanges) > 1 and not first.text.endswith(TRIM_NOTE):
        first = replace(first, text=first.text + TRIM_NOTE)
    while len(exchanges) > 1:
        exchanges.pop(0)
        if estimate(_join(first, exchanges)) <= limit:
            return _join(first, exchanges)

    if exchanges:
        exchanges[0] = [_shorten(m) for m in exchanges[0]]
    trimmed = _join(first, exchanges)
    if estimate(trimmed) <= limit:
        return trimmed
    raise ContextTooLarge(
        f"The conversation does not fit the model's context window even after trimming "
        f"(about {estimate(trimmed)} tokens, limit {limit})"
    )


def _split(messages: Sequence[Message]) -> tuple[Message, list[list[Message]]]:
    first, *rest = messages
    exchanges: list[list[Message]] = []
    for m in rest:
        if m.role == "assistant" or not exchanges:
            exchanges.append([m])
        else:
            exchanges[-1].append(m)
    return first, exchanges


def _join(first: Message, exchanges: list[list[Message]]) -> list[Message]:
    return [first, *(m for exchange in exchanges for m in exchange)]


def _shorten(m: Message) -> Message:
    if m.provider_data:
        m = replace(m, provider_data=None)
    r = m.tool_result
    if r is not None and message_chars(m) > SHORT_RESULT:
        placeholder = f"[tool result removed to save context: {message_chars(m)} characters]"
        m = replace(m, tool_result=ToolResult(r.call_id, placeholder, r.is_error))
    return m
