"""Tracer on the OpenTelemetry SDK (ADR 0022), and where its spans go.

`TRACING_EXPORTER`:

- `none`: spans and trace context are made (so `traceparent` still reaches the control plane and
  the gateway), nothing is uploaded;
- `logging`: each span printed as JSON on stdout (local checks);
- `xray`: OTLP over HTTPS to `https://xray.<region>.amazonaws.com/v1/traces`, signed with SigV4
  (service `xray`) from the default AWS credentials (the task role on Fargate). Needs Transaction
  Search on in the account. Spans go in batches from a background thread; `shutdown()` sends what
  is left (SIGTERM on scale-in).

Every span is kept: a run's trace must be whole, and a run makes tens of spans.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from typing import Any

import requests
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.credentials import Credentials
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
    SpanExporter,
)
from opentelemetry.sdk.trace.sampling import ALWAYS_ON
from opentelemetry.trace import Status, StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from worker.ports.tracing import AttributeValue, Tracer

EXPORTERS = ("none", "logging", "xray")
SERVICE_NAME = "worker"
SCOPE = "worker"
_PROPAGATOR = TraceContextTextMapPropagator()


class _Span:
    def __init__(self, span: trace.Span) -> None:
        self._span = span

    def set(self, key: str, value: AttributeValue) -> None:
        self._span.set_attribute(key, value)


class OtelTracer:
    """The tracing port on an OpenTelemetry `TracerProvider` (nothing global is set)."""

    def __init__(self, provider: TracerProvider) -> None:
        self._provider = provider
        self._tracer = provider.get_tracer(SCOPE)

    @contextmanager
    def span(
        self,
        name: str,
        attributes: Mapping[str, AttributeValue] | None = None,
        *,
        parent: str | None = None,
    ) -> Iterator[_Span]:
        context = _PROPAGATOR.extract({"traceparent": parent}) if parent else None
        with self._tracer.start_as_current_span(
            name,
            context=context,
            attributes=dict(attributes or {}),
            record_exception=False,  # exception messages can carry data
            set_status_on_exception=False,
        ) as span:
            try:
                yield _Span(span)
            except BaseException as e:
                span.set_status(Status(StatusCode.ERROR, type(e).__name__))
                raise

    def annotate(self, attributes: Mapping[str, AttributeValue]) -> None:
        span = trace.get_current_span()
        if span.get_span_context().is_valid:
            span.set_attributes(dict(attributes))

    def traceparent(self) -> str | None:
        carrier: dict[str, str] = {}
        _PROPAGATOR.inject(carrier)
        return carrier.get("traceparent")

    def shutdown(self) -> None:
        """Sends the spans still waiting and stops the exporter."""
        self._provider.shutdown()


def trace_headers(tracer: Tracer) -> Callable[[], dict[str, str]]:
    """Headers that carry the current span to another service (`traceparent`), for adapters."""

    def headers() -> dict[str, str]:
        value = tracer.traceparent()
        return {"traceparent": value} if value else {}

    return headers


def build_tracer(
    exporter: str,
    region: str,
    *,
    credentials: Callable[[], Credentials] | None = None,
    span_exporter: SpanExporter | None = None,
) -> OtelTracer:
    """A tracer for `TRACING_EXPORTER` (see the module docs). `span_exporter` replaces the
    exporter (tests); `credentials` replaces the default AWS credentials for `xray`."""
    provider = TracerProvider(
        resource=Resource.create({"service.name": SERVICE_NAME}), sampler=ALWAYS_ON
    )
    if span_exporter is not None:
        provider.add_span_processor(SimpleSpanProcessor(span_exporter))
    elif exporter == "logging":
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
    elif exporter == "xray":
        provider.add_span_processor(BatchSpanProcessor(xray_exporter(region, credentials)))
    elif exporter != "none":
        raise ValueError(f"unknown exporter {exporter!r}; expected one of {', '.join(EXPORTERS)}")
    return OtelTracer(provider)


def xray_exporter(
    region: str, credentials: Callable[[], Credentials] | None = None, endpoint: str | None = None
) -> OTLPSpanExporter:
    """OTLP/HTTP exporter to X-Ray's OTLP endpoint, each request signed with SigV4."""
    session = requests.Session()
    session.auth = SigV4RequestsAuth("xray", region, credentials or _DefaultCredentials())
    return OTLPSpanExporter(
        endpoint=endpoint or f"https://xray.{region}.amazonaws.com/v1/traces", session=session
    )


class SigV4RequestsAuth(requests.auth.AuthBase):
    """Signs a `requests` request with SigV4 (host, content type and the exact body)."""

    def __init__(self, service: str, region: str, credentials: Callable[[], Credentials]) -> None:
        self._service = service
        self._region = region
        self._credentials = credentials

    def __call__(self, request: requests.PreparedRequest) -> requests.PreparedRequest:
        body = request.body or b""
        if isinstance(body, str):
            body = body.encode()
        headers = {
            k: v
            for k, v in request.headers.items()
            if k.lower() in ("content-type", "content-encoding")
        }
        aws = AWSRequest(method=request.method, url=request.url, data=body, headers=headers)
        SigV4Auth(self._credentials(), self._service, self._region).add_auth(aws)
        for name in ("Authorization", "X-Amz-Date", "X-Amz-Security-Token"):
            if name in aws.headers:
                request.headers[name] = aws.headers[name]
        return request


class _DefaultCredentials:
    """boto3's default chain (the task role on Fargate), resolved once; refreshes itself."""

    def __init__(self) -> None:
        self._found: Any = None

    def __call__(self) -> Any:
        if self._found is None:
            import boto3

            self._found = boto3.Session().get_credentials()
            if self._found is None:
                raise RuntimeError("no AWS credentials for the X-Ray exporter")
        return self._found.get_frozen_credentials()
