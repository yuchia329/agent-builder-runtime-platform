"""The OpenTelemetry tracer (ADR 0022): trace continuation, failure marking, and the SigV4-signed
upload to X-Ray's OTLP endpoint."""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from botocore.credentials import Credentials
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from worker.adapters.tracing_otel import build_tracer, trace_headers, xray_exporter

TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"
PARENT_SPAN = "00f067aa0ba902b7"
TRACE_PARENT = f"00-{TRACE_ID}-{PARENT_SPAN}-01"


@pytest.fixture
def spans():
    return InMemorySpanExporter()


@pytest.fixture
def tracer(spans):
    return build_tracer("none", "us-east-1", span_exporter=spans)


def test_span_continues_the_run_trace_and_is_what_calls_carry(tracer, spans):
    with (
        tracer.span("run.attempt", {"run.id": "run_1"}, parent=TRACE_PARENT),
        tracer.span("model.call", {"run.step": 1}) as span,
    ):
        span.set("gen_ai.usage.input_tokens", 120)
        sent = tracer.traceparent()

    model_call, attempt = spans.get_finished_spans()
    assert attempt.name == "run.attempt"
    assert format(attempt.context.trace_id, "032x") == TRACE_ID
    assert format(attempt.parent.span_id, "016x") == PARENT_SPAN
    assert model_call.parent.span_id == attempt.context.span_id
    assert model_call.attributes["gen_ai.usage.input_tokens"] == 120
    assert sent == f"00-{TRACE_ID}-{format(model_call.context.span_id, '016x')}-01"


def test_without_a_parent_a_span_starts_a_new_trace(tracer, spans):
    with tracer.span("run.attempt"):
        pass

    [span] = spans.get_finished_spans()
    assert span.parent is None
    assert span.context.trace_id != int(TRACE_ID, 16)


def test_annotate_adds_to_the_current_span_only_inside_one(tracer, spans):
    tracer.annotate({"agent.name": "ignored"})  # no current span: nothing happens
    with tracer.span("run.attempt"):
        tracer.annotate({"agent.name": "issue-triage", "agent.version": 3})

    [span] = spans.get_finished_spans()
    assert span.attributes["agent.name"] == "issue-triage"
    assert span.attributes["agent.version"] == 3


def test_a_failure_records_the_exception_type_never_its_message(tracer, spans):
    with pytest.raises(ValueError), tracer.span("tool.call"):
        raise ValueError("token=secret-value")

    [span] = spans.get_finished_spans()
    assert span.status.status_code is StatusCode.ERROR
    assert span.status.description == "ValueError"
    assert span.events == ()  # no exception event with the message


def test_outside_a_span_nothing_is_propagated(tracer):
    assert tracer.traceparent() is None
    assert trace_headers(tracer)() == {}
    with tracer.span("run.attempt", parent=TRACE_PARENT):
        assert trace_headers(tracer)()["traceparent"].startswith(f"00-{TRACE_ID}-")


def test_unknown_exporter_is_refused():
    with pytest.raises(ValueError, match="unknown exporter"):
        build_tracer("otlp", "us-east-1")


class Collector:
    """Answers OTLP POSTs with 200 after recording them."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, dict[str, str], bytes]] = []
        collector = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:
                pass

            def do_POST(self) -> None:  # noqa: N802
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                collector.requests.append((self.path, dict(self.headers.items()), body))
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}/v1/traces"

    def __enter__(self) -> "Collector":
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc) -> None:
        self._server.shutdown()


def test_xray_upload_is_otlp_signed_with_sigv4_for_xray():
    credentials = Credentials("AKIDEXAMPLE", "secret", "session")
    with Collector() as collector:
        exporter = xray_exporter("us-east-1", lambda: credentials, endpoint=collector.url)
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        with provider.get_tracer("test").start_as_current_span("model.call"):
            pass
        provider.shutdown()

    path, raw_headers, body = collector.requests[0]
    headers = {k.lower(): v for k, v in raw_headers.items()}
    assert path == "/v1/traces"
    assert headers["content-type"] == "application/x-protobuf"
    authorization = headers["authorization"]
    assert authorization.startswith("AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/")
    assert "/us-east-1/xray/aws4_request" in authorization
    signed = authorization.split("SignedHeaders=")[1].split(",")[0].split(";")
    assert {"content-type", "host", "x-amz-date"} <= set(signed)
    assert headers["x-amz-security-token"] == "session"
    assert b"model.call" in body  # the OTLP protobuf request holds the span


def test_default_xray_endpoint_is_the_regional_otlp_endpoint():
    exporter = xray_exporter("eu-west-1", lambda: Credentials("a", "b"))
    assert exporter._endpoint == "https://xray.eu-west-1.amazonaws.com/v1/traces"
