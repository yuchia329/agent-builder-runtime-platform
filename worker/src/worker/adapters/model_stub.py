"""Scripted model: plays back a fixed list of turns. No network; used by tests and load tests."""

from __future__ import annotations

import itertools
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from worker.core.errors import ModelError
from worker.core.types import (
    Message,
    ModelTurn,
    StopReason,
    ToolCall,
    ToolSpec,
    Usage,
)

_ids = itertools.count(1)


def tool_turn(*calls: tuple[str, dict[str, Any]], text: str = "") -> ModelTurn:
    """A turn that asks for one or more tool calls: tool_turn(("github.add_label", {...}))."""
    return ModelTurn(
        StopReason.TOOL_USE,
        text=text,
        tool_calls=tuple(ToolCall(f"call_{next(_ids)}", name, args) for name, args in calls),
        usage=Usage(input_tokens=100, output_tokens=20),
    )


def final_turn(text: str) -> ModelTurn:
    return ModelTurn(
        StopReason.END_TURN, text=text, usage=Usage(input_tokens=100, output_tokens=20)
    )


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
    """

    def __init__(self, turns: Sequence[ModelTurn]) -> None:
        self._turns = list(turns)

    def complete(
        self, model: str, system: str, messages: Sequence[Message], tools: Sequence[ToolSpec]
    ) -> ModelTurn:
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
