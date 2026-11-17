import pytest

from worker.core.budget import BudgetExceeded, BudgetTracker
from worker.core.types import Budget, FailureReason


def test_allows_steps_up_to_the_limit(clock):
    tracker = BudgetTracker(Budget(max_steps=2, max_usd=1, max_seconds=60), clock)

    for _ in range(2):
        tracker.check()
        tracker.record_step()

    with pytest.raises(BudgetExceeded) as e:
        tracker.check()
    assert e.value.reason is FailureReason.BUDGET_STEPS


def test_stops_when_time_is_up(clock):
    tracker = BudgetTracker(Budget(max_steps=100, max_usd=1, max_seconds=30), clock)
    clock.advance(29.9)
    tracker.check()

    clock.advance(0.1)

    with pytest.raises(BudgetExceeded) as e:
        tracker.check()
    assert e.value.reason is FailureReason.BUDGET_SECONDS


def test_stops_once_cost_reaches_max_usd(clock):
    tracker = BudgetTracker(Budget(max_steps=100, max_usd=0.10, max_seconds=60), clock)
    tracker.check()
    tracker.record_step(cost_usd=0.06)
    tracker.check()
    tracker.record_step(cost_usd=0.05)  # one turn may overshoot; the next check stops the run

    with pytest.raises(BudgetExceeded) as e:
        tracker.check()
    assert e.value.reason is FailureReason.BUDGET_USD
    assert tracker.cost_usd == pytest.approx(0.11)
