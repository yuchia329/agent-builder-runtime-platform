"""StubModel load-test settings: a fixed wait before each answer (M7), never in tests' real time;
a share of calls still rate limited and a price per call (M8)."""

import pytest

from worker.adapters.model_stub import STUB_USAGE, StubModel, demo_turns, price_per_call
from worker.core.errors import ModelRateLimited
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


def test_a_share_of_calls_is_rate_limited():
    draws = iter([0.05, 0.5, 0.09])
    model = StubModel(demo_turns(), rate_limited_share=0.1, draw=lambda: next(draws))

    with pytest.raises(ModelRateLimited, match="MODEL_STUB_429_RATE"):
        model.complete("stub/demo", "x", _conversation(), [])
    assert model.complete("stub/demo", "x", _conversation(), []).tool_calls
    with pytest.raises(ModelRateLimited):
        model.complete("stub/demo", "x", _conversation(), [])


def test_no_rate_limits_by_default():
    model = StubModel(demo_turns(), draw=lambda: 0.0)

    assert model.complete("stub/demo", "x", _conversation(), []).tool_calls


@pytest.mark.parametrize("share", [-0.1, 1.1])
def test_share_outside_zero_to_one_is_refused(share):
    with pytest.raises(ValueError):
        StubModel(demo_turns(), rate_limited_share=share)


@pytest.mark.parametrize("usd", [0.0, 0.0001, 0.05, 1.0])
def test_price_per_call_makes_each_stub_call_cost_that_much(usd):
    assert price_per_call(usd).cost_usd(STUB_USAGE) == pytest.approx(usd)
