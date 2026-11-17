from typing import Protocol


class Clock(Protocol):
    """Time source; injectable so tests control time."""

    def monotonic(self) -> float:
        """Seconds from an arbitrary start, never going back (budgets, elapsed time)."""
        ...

    def time(self) -> float:
        """Wall-clock seconds since the Unix epoch (lease expiry, TTL), shared across workers."""
        ...
