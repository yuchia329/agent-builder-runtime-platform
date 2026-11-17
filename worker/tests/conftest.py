import pytest


class FakeClock:
    """Monotonic clock the test moves by hand."""

    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now

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
