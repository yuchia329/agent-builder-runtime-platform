"""Metrics as CloudWatch embedded metric format (EMF) lines on stdout (ADR 0023).

On Fargate the awslogs driver ships each stdout line as a log event; CloudWatch reads the `_aws`
block and records the metric, with no API call and no AWS SDK. Logs go to stderr, so a line here
is never mixed with a log record. One line per value:

    {"_aws": {"Timestamp": 1763600000000, "CloudWatchMetrics": [{"Namespace": "AgentPlatform/dev",
     "Dimensions": [[]], "Metrics": [{"Name": "ModelCostUsd", "Unit": "None"}]}]},
     "ModelCostUsd": 0.0012}

`Dimensions: [[]]` (one empty set) makes a metric without dimensions, the one the alarm watches.
The namespace is checked by `config.py` (METRICS_NAMESPACE).
"""

from __future__ import annotations

import json
import logging
import sys
import threading
from typing import TextIO

from worker.adapters.clock_system import SystemClock
from worker.ports.clock import Clock

log = logging.getLogger(__name__)

MODEL_COST = "ModelCostUsd"


class EmfMetrics:
    def __init__(
        self, namespace: str, stream: TextIO | None = None, clock: Clock | None = None
    ) -> None:
        self._namespace = namespace
        self._stream = stream  # None: sys.stdout at write time (tests may replace it)
        self._clock = clock or SystemClock()
        self._lock = threading.Lock()  # runs share a task: one whole line at a time

    def model_cost(self, usd: float) -> None:
        self._write(MODEL_COST, round(float(usd), 6))

    def _write(self, name: str, value: float) -> None:
        line = json.dumps(
            {
                "_aws": {
                    "Timestamp": int(self._clock.time() * 1000),
                    "CloudWatchMetrics": [
                        {
                            "Namespace": self._namespace,
                            "Dimensions": [[]],
                            "Metrics": [{"Name": name, "Unit": "None"}],
                        }
                    ],
                },
                name: value,
            },
            separators=(",", ":"),
        )
        try:
            with self._lock:
                stream = self._stream or sys.stdout
                stream.write(line + "\n")
                stream.flush()
        except Exception as e:  # noqa: BLE001 - a lost metric must not fail the run
            log.warning("could not write metric %s: %s", name, type(e).__name__)
