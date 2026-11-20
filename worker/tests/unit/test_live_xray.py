"""Live check against dev AWS (ADR 0022): one span through the X-Ray OTLP endpoint, found again in
the `aws/spans` log group (Transaction Search). Skipped unless LIVE_AWS=1; uses the AWS profile
`default` (never `prod`); costs a fraction of a cent.

    LIVE_AWS=1 uv run pytest tests/unit/test_live_xray.py -s
"""

import os
import time

import pytest

from worker.adapters.tracing_otel import build_tracer

pytestmark = pytest.mark.skipif(os.environ.get("LIVE_AWS") != "1", reason="set LIVE_AWS=1")

REGION = "us-east-1"
PROFILE = "default"
WAIT_SECONDS = 240


def test_a_span_reaches_transaction_search():
    import boto3
    from opentelemetry import trace

    session = boto3.Session(profile_name=PROFILE, region_name=REGION)
    assert session.client("xray").get_trace_segment_destination()["Destination"] == (
        "CloudWatchLogs"
    ), "Transaction Search is not on"
    credentials = session.get_credentials()
    tracer = build_tracer("xray", REGION, credentials=credentials.get_frozen_credentials)

    with tracer.span("live.check", {"run.id": "live-check"}):
        trace_id = format(trace.get_current_span().get_span_context().trace_id, "032x")
    tracer.shutdown()  # sends the batch now
    print(f"trace {trace_id}")

    logs = session.client("logs")
    started = int((time.time() - 300) * 1000)
    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline:
        events = logs.filter_log_events(
            logGroupName="aws/spans",
            startTime=started,
            filterPattern=f'{{ $.traceId = "{trace_id}" }}',
        )["events"]
        if events:
            print(f"found after {WAIT_SECONDS - (deadline - time.monotonic()):.0f} s")
            assert '"live.check"' in events[0]["message"]
            return
        time.sleep(15)
    pytest.fail(f"span of trace {trace_id} not in aws/spans after {WAIT_SECONDS} s")
