"""Tracing port (ADR 0022): spans for a run's trace, which the control plane started.

Core opens spans through this port and never sees the tracing library. Adapters: OpenTelemetry
(`adapters/tracing_otel.py`), no-op (`core/tracing.py`, the default).

Attributes are ids, counts and names only: never run input, output, tool arguments or results,
tokens, keys or headers.
"""

from collections.abc import Mapping
from contextlib import AbstractContextManager
from typing import Protocol

AttributeValue = str | int | float | bool


class Span(Protocol):
    def set(self, key: str, value: AttributeValue) -> None: ...


class Tracer(Protocol):
    def span(
        self,
        name: str,
        attributes: Mapping[str, AttributeValue] | None = None,
        *,
        parent: str | None = None,
    ) -> AbstractContextManager[Span]:
        """A span, current while the block runs; a child of the current span, or of `parent`
        (a W3C `traceparent`) when given. An exception leaving the block marks the span failed
        (with the exception's type only) and propagates."""
        ...

    def annotate(self, attributes: Mapping[str, AttributeValue]) -> None:
        """Adds attributes to the current span (nothing if there is none)."""
        ...

    def traceparent(self) -> str | None:
        """The current span as a W3C `traceparent` header value, or None outside a span."""
        ...
