"""Per-run limits on steps and wall time. Dollars are added in M1 with model pricing."""

from worker.core.types import Budget, FailureReason
from worker.ports.clock import Clock


class BudgetExceeded(Exception):
    def __init__(self, reason: FailureReason, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


class BudgetTracker:
    """Counts steps (one model turn or one tool call) and elapsed seconds.

    Call ``check()`` before starting a step and ``record_step()`` after it finishes, so a run
    never starts a step it is not allowed to finish counting.
    """

    def __init__(self, budget: Budget, clock: Clock, steps_done: int = 0) -> None:
        self._budget = budget
        self._clock = clock
        self._started = clock.monotonic()
        self.steps = steps_done

    def check(self) -> None:
        if self.steps >= self._budget.max_steps:
            raise BudgetExceeded(
                FailureReason.BUDGET_STEPS,
                f"Step budget used up: {self.steps} of {self._budget.max_steps} steps",
            )
        elapsed = self._clock.monotonic() - self._started
        if elapsed >= self._budget.max_seconds:
            raise BudgetExceeded(
                FailureReason.BUDGET_SECONDS,
                f"Time budget used up: {elapsed:.1f} of {self._budget.max_seconds} seconds",
            )

    def record_step(self) -> None:
        self.steps += 1
