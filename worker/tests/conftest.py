import pytest


class FakeClock:
    """Clock the test moves by hand; `time()` is a fixed epoch plus the same offset."""

    EPOCH = 1_800_000_000.0

    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now

    def time(self) -> float:
        return self.EPOCH + self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def fake_control_plane_server():
    from fake_control_plane_server import FakeControlPlaneServer

    with FakeControlPlaneServer() as server:
        yield server
