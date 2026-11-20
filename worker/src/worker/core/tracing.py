"""The default tracer: makes no spans (tests, and TRACING_EXPORTER unset in older wiring)."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager

from worker.ports.tracing import AttributeValue


class _NoopSpan:
    def set(self, key: str, value: AttributeValue) -> None:
        pass


_SPAN = _NoopSpan()


class NoopTracer:
    @contextmanager
    def span(
        self,
        name: str,
        attributes: Mapping[str, AttributeValue] | None = None,
        *,
        parent: str | None = None,
    ) -> Iterator[_NoopSpan]:
        yield _SPAN

    def annotate(self, attributes: Mapping[str, AttributeValue]) -> None:
        pass

    def traceparent(self) -> str | None:
        return None
