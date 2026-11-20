"""The EMF line CloudWatch reads (ADR 0023): namespace, no dimensions, timestamp, value only."""

import io
import json
import threading

from worker.adapters.metrics_emf import EmfMetrics


class Clock:
    def time(self) -> float:
        return 1_800_000_000.25

    def monotonic(self) -> float:
        return 0.0


def _lines(stream: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in stream.getvalue().splitlines()]


def test_one_line_per_model_call_in_embedded_metric_format():
    out = io.StringIO()
    metrics = EmfMetrics("AgentPlatform/dev", stream=out, clock=Clock())

    metrics.model_cost(0.0000541234)
    metrics.model_cost(0)

    assert _lines(out) == [
        {
            "_aws": {
                "Timestamp": 1_800_000_000_250,
                "CloudWatchMetrics": [
                    {
                        "Namespace": "AgentPlatform/dev",
                        "Dimensions": [[]],
                        "Metrics": [{"Name": "ModelCostUsd", "Unit": "None"}],
                    }
                ],
            },
            "ModelCostUsd": 0.000054,
        },
        {
            "_aws": {
                "Timestamp": 1_800_000_000_250,
                "CloudWatchMetrics": [
                    {
                        "Namespace": "AgentPlatform/dev",
                        "Dimensions": [[]],
                        "Metrics": [{"Name": "ModelCostUsd", "Unit": "None"}],
                    }
                ],
            },
            "ModelCostUsd": 0.0,
        },
    ]


def test_each_line_is_compact_json_on_its_own_line():
    out = io.StringIO()
    EmfMetrics("AgentPlatform/dev", stream=out).model_cost(0.5)

    text = out.getvalue()
    assert text.endswith("\n") and text.count("\n") == 1
    assert ": " not in text and ", " not in text


def test_lines_from_many_threads_never_mix():
    out = io.StringIO()
    metrics = EmfMetrics("AgentPlatform/dev", stream=out)
    threads = [
        threading.Thread(target=lambda: [metrics.model_cost(0.001) for _ in range(200)])
        for _ in range(8)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    lines = _lines(out)
    assert len(lines) == 1600
    assert all(line["ModelCostUsd"] == 0.001 for line in lines)


def test_writes_to_stdout_by_default(capsys):
    EmfMetrics("AgentPlatform/dev").model_cost(0.25)

    captured = capsys.readouterr()
    assert json.loads(captured.out)["ModelCostUsd"] == 0.25
    assert captured.err == ""


def test_a_failed_write_is_logged_without_the_value(caplog):
    class Broken(io.StringIO):
        def write(self, s):
            raise OSError("closed")

    EmfMetrics("AgentPlatform/dev", stream=Broken()).model_cost(0.25)

    assert "could not write metric ModelCostUsd: OSError" in caplog.text
