from collections.abc import Sequence
from typing import Protocol

from worker.core.types import Message, ModelTurn, ToolSpec


class Model(Protocol):
    """One model turn: conversation and tools in; text or tool calls plus token usage out.

    Adapters: scripted stub (tests, load tests); Bedrock in M1. Adapters retry transient vendor
    errors themselves and raise ModelError when they give up.
    """

    def complete(
        self, system: str, messages: Sequence[Message], tools: Sequence[ToolSpec]
    ) -> ModelTurn: ...
