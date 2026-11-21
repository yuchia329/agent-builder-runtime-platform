"""Scripted model: plays back a fixed list of turns. No network; used by tests and load tests."""

from __future__ import annotations

import itertools
import random
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from worker.core.errors import ModelError, ModelRateLimited
from worker.core.models import Price
from worker.core.types import (
    Message,
    ModelTurn,
    StopReason,
    ToolCall,
    ToolSpec,
    Usage,
)

_ids = itertools.count(1)
STUB_USAGE = Usage(input_tokens=100, output_tokens=20)  # every scripted turn reports this


def price_per_call(usd: float) -> Price:
    """A price that makes every stub call cost `usd` (MODEL_STUB_PRICE_USD; load tests, M8)."""
    per_million = usd * 1_000_000 / (STUB_USAGE.input_tokens + STUB_USAGE.output_tokens)
    return Price(per_million, per_million)


def tool_turn(*calls: tuple[str, dict[str, Any]], text: str = "") -> ModelTurn:
    """A turn that asks for one or more tool calls: tool_turn(("github.add_label", {...}))."""
    return ModelTurn(
        StopReason.TOOL_USE,
        text=text,
        tool_calls=tuple(ToolCall(f"call_{next(_ids)}", name, args) for name, args in calls),
        usage=STUB_USAGE,
    )


def final_turn(text: str) -> ModelTurn:
    return ModelTurn(StopReason.END_TURN, text=text, usage=STUB_USAGE)


@dataclass(frozen=True)
class ModelCall:
    """What the model received on one call, for assertions."""

    model: str
    system: str
    messages: tuple[Message, ...]
    tools: tuple[ToolSpec, ...]


class ScriptedModel:
    def __init__(self, turns: Sequence[ModelTurn]) -> None:
        self._turns = list(turns)
        self.calls: list[ModelCall] = []

    def complete(
        self, model: str, system: str, messages: Sequence[Message], tools: Sequence[ToolSpec]
    ) -> ModelTurn:
        self.calls.append(ModelCall(model, system, tuple(messages), tuple(tools)))
        if len(self.calls) > len(self._turns):
            raise ModelError(f"Script has only {len(self._turns)} turns")
        return self._turns[len(self.calls) - 1]


class StubModel:
    """Stateless scripted model for a running worker: one instance serves every run.

    The reply is picked by how many replies the model already gave in this conversation, so
    concurrent runs never share a position in the script. Tool call ids are derived from that
    position, so replays are deterministic. Past the end of the script it gives a final answer.

    `latency_seconds` makes every call wait that long before answering, like a real model
    (MODEL_STUB_LATENCY_MS; load tests, M7). `sleep` is injectable so tests never wait.

    `rate_limited_share` of calls (0 to 1, MODEL_STUB_429_RATE; load tests, M8) raise
    ModelRateLimited after the wait, as a provider still answering 429 after the retries would.
    `draw` (a number from 0 to 1) is injectable so tests are deterministic.
    """

    def __init__(
        self,
        turns: Sequence[ModelTurn],
        latency_seconds: float = 0.0,
        sleep: Callable[[float], None] = time.sleep,
        *,
        rate_limited_share: float = 0.0,
        draw: Callable[[], float] = random.random,
    ) -> None:
        if latency_seconds < 0:
            raise ValueError("latency_seconds must not be negative")
        if not 0 <= rate_limited_share <= 1:
            raise ValueError("rate_limited_share must be from 0 to 1")
        self._turns = list(turns)
        self._latency = latency_seconds
        self._sleep = sleep
        self._rate_limited_share = rate_limited_share
        self._draw = draw

    def complete(
        self, model: str, system: str, messages: Sequence[Message], tools: Sequence[ToolSpec]
    ) -> ModelTurn:
        if self._latency > 0:
            self._sleep(self._latency)
        if self._rate_limited_share > 0 and self._draw() < self._rate_limited_share:
            raise ModelRateLimited("Stub model: rate limited (MODEL_STUB_429_RATE)")
        position = sum(1 for m in messages if m.role == "assistant")
        if position >= len(self._turns):
            return final_turn("Stub model: script finished.")
        turn = self._turns[position]
        calls = tuple(
            ToolCall(f"stub_{position}_{i}", c.name, c.arguments)
            for i, c in enumerate(turn.tool_calls)
        )
        return ModelTurn(turn.stop_reason, turn.text, calls, turn.usage)


def demo_turns() -> list[ModelTurn]:
    """A short issue-triage conversation for the local demo (fake tools, example spec)."""
    repo = "acme/payments"
    return [
        tool_turn(("github.search_issues", {"repo": repo, "query": "refund EUR"})),
        tool_turn(("github.add_label", {"repo": repo, "issue_number": 42, "label": "bug"})),
        final_turn("Labeled #42 as bug. Possible duplicate of #7 (Refunds fail for EUR cards)."),
    ]
