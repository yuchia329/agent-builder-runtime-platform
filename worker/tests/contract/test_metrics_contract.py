"""Behavior every Metrics adapter must have (ADR 0023): accept any cost, including 0, from many
threads, and never raise, even when the value cannot be written."""

import io
import threading

import pytest

from worker.adapters.metrics_emf import EmfMetrics
from worker.core.metrics import NoopMetrics


class BrokenStream(io.StringIO):
    def write(self, s):
        raise OSError("stdout closed")


def _noop(broken):
    return NoopMetrics()


def _emf(broken):
    return EmfMetrics("AgentPlatform/test", stream=BrokenStream() if broken else io.StringIO())


ADAPTERS = [pytest.param(_noop, id="noop"), pytest.param(_emf, id="emf")]


@pytest.mark.parametrize("make", ADAPTERS)
@pytest.mark.parametrize("usd", [0.0, 0.000054, 12.5])
def test_accepts_a_cost(make, usd):
    make(False).model_cost(usd)


@pytest.mark.parametrize("make", ADAPTERS)
def test_never_raises_when_the_value_cannot_be_written(make):
    make(True).model_cost(0.01)


@pytest.mark.parametrize("make", ADAPTERS)
def test_concurrent_calls(make):
    metrics = make(False)
    threads = [
        threading.Thread(target=lambda: [metrics.model_cost(0.001) for _ in range(50)])
        for _ in range(4)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
