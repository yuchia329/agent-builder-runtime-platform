"""Routes each model call to the adapter of the spec's provider (ADR 0010)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from worker.core.errors import ModelError
from worker.core.types import Message, ModelTurn, ToolSpec
from worker.ports.model import Model


class ModelRouter:
    """A Model that splits ``<provider>/<model id>`` at the first '/' and forwards the model id
    to that provider's adapter. An unknown provider raises ModelError, which fails the run."""

    def __init__(self, providers: Mapping[str, Model]) -> None:
        self._providers = dict(providers)

    def complete(
        self, model: str, system: str, messages: Sequence[Message], tools: Sequence[ToolSpec]
    ) -> ModelTurn:
        provider, slash, model_id = model.partition("/")
        if not slash or not model_id:
            raise ModelError(f"Model '{model}' is not in the form <provider>/<model id>")
        adapter = self._providers.get(provider)
        if adapter is None:
            configured = ", ".join(sorted(self._providers)) or "none"
            raise ModelError(
                f"Model provider '{provider}' is not configured in this worker "
                f"(configured: {configured})"
            )
        return adapter.complete(model_id, system, messages, tools)
