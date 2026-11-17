import time


class SystemClock:
    def monotonic(self) -> float:
        return time.monotonic()
