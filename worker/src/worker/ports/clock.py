from typing import Protocol


class Clock(Protocol):
    """Elapsed-time source for budgets; injectable so tests control time."""

    def monotonic(self) -> float: ...
