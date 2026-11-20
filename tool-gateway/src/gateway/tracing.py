"""Spans for the run's trace (ADR 0022): each `/mcp` request continues the caller's `traceparent`
(the worker's "tool.call" span), so the gateway's spans join the run's one trace.

`TRACING_EXPORTER`:

- `none` (default, also on Lambda): spans and trace context are made, nothing is uploaded. The
  worker's spans already time each call; the access log's `trace_id` links a line to its trace;
- `logging`: each span printed as JSON on stdout (local checks).

Span attributes are ids, names, counts and status codes only: never arguments, results, URLs,
tokens or exception messages (a failure records the exception type). `traceparent` goes to the
control plane, never to a connector's API.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor, SpanExporter
from opentelemetry.trace import Status, StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from gateway.auth.run_token import RunClaims

EXPORTERS = ("none", "logging")
SERVICE_NAME = "tool-gateway"
TRACEPARENT = "traceparent"
_PROPAGATOR = TraceContextTextMapPropagator()

AttributeValue = str | int | float | bool


class Tracing:
    """Spans on an own `TracerProvider` (nothing global is set)."""

    def __init__(self, provider: TracerProvider) -> None:
        self._provider = provider
        self._tracer = provider.get_tracer("tool-gateway")

    @contextmanager
    def span(
        self,
        name: str,
        attributes: Mapping[str, AttributeValue | None] | None = None,
        *,
        traceparent: str | None = None,
    ) -> Iterator[trace.Span]:
        """A span, child of the current one, or of `traceparent` when given (an incoming
        request). An exception ends it with status ERROR and the exception's type name."""
        context = _PROPAGATOR.extract({TRACEPARENT: traceparent}) if traceparent else None
        with self._tracer.start_as_current_span(
            name,
            context=context,
            attributes=_clean(attributes),
            record_exception=False,  # exception messages can carry data
            set_status_on_exception=False,
        ) as span:
            try:
                yield span
            except BaseException as e:
                span.set_status(Status(StatusCode.ERROR, type(e).__name__))
                raise

    def shutdown(self) -> None:
        self._provider.shutdown()


def build_tracing(exporter: str = "none", span_exporter: SpanExporter | None = None) -> Tracing:
    """Tracing for `TRACING_EXPORTER` (module docs); `span_exporter` replaces it (tests)."""
    if exporter not in EXPORTERS:
        raise ValueError(f"unknown exporter {exporter!r}; expected one of {', '.join(EXPORTERS)}")
    provider = TracerProvider(resource=Resource.create({"service.name": SERVICE_NAME}))
    if span_exporter is not None:
        provider.add_span_processor(SimpleSpanProcessor(span_exporter))
    elif exporter == "logging":
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
    return Tracing(provider)


def run_attributes(claims: RunClaims) -> dict[str, AttributeValue]:
    return {
        "run.id": claims.run_id,
        "tenant.id": claims.tenant,
        "agent.name": claims.agent,
        "agent.version": claims.agent_version,
    }


def traceparent() -> str | None:
    """The current span as a `traceparent` value, for calls to the control plane."""
    carrier: dict[str, str] = {}
    _PROPAGATOR.inject(carrier)
    return carrier.get(TRACEPARENT)


def trace_id() -> str | None:
    """The current trace id (32 hex), for log lines; None outside a span."""
    context = trace.get_current_span().get_span_context()
    return format(context.trace_id, "032x") if context.is_valid else None


def _clean(attributes: Mapping[str, AttributeValue | None] | None) -> dict[str, AttributeValue]:
    return {k: v for k, v in (attributes or {}).items() if v is not None}
