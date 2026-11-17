from collections.abc import Sequence
from typing import Protocol

from worker.core.types import Message, ModelTurn, ToolSpec


class Model(Protocol):
    """One model turn: model, conversation and tools in; text or tool calls plus token usage out.

    ``model`` is the spec's ``<provider>/<model id>``; the router strips the provider before
    calling a provider's adapter, which receives only the model id (ADR 0010). Adapters retry
    transient vendor errors themselves and raise ModelError when they give up.
    """

    def complete(
        self, model: str, system: str, messages: Sequence[Message], tools: Sequence[ToolSpec]
    ) -> ModelTurn: ...
