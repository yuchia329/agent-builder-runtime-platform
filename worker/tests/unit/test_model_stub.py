"""StubModel latency (M7 load tests): a fixed wait before each answer, never in tests' real time."""

import pytest

from worker.adapters.model_stub import StubModel, demo_turns
from worker.core.types import Message


def _conversation():
    return [Message.user("Issue 42: refund fails")]


def test_waits_the_latency_before_every_answer():
    waits = []
    model = StubModel(demo_turns(), latency_seconds=1.5, sleep=waits.append)

    first = model.complete("stub/demo", "Triage.", _conversation(), [])
    model.complete("stub/demo", "Triage.", [*_conversation(), Message.assistant(first)], [])

    assert waits == [1.5, 1.5]


def test_no_wait_by_default():
    waits = []
    StubModel(demo_turns(), sleep=waits.append).complete(
        "stub/demo", "Triage.", _conversation(), []
    )

    assert waits == []


def test_latency_does_not_change_the_reply():
    plain = StubModel(demo_turns()).complete("stub/demo", "x", _conversation(), [])
    slow = StubModel(demo_turns(), 2.0, sleep=lambda s: None).complete(
        "stub/demo", "x", _conversation(), []
    )

    assert slow == plain


def test_negative_latency_is_refused():
    with pytest.raises(ValueError):
        StubModel(demo_turns(), latency_seconds=-1)
