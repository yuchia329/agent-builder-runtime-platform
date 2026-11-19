import pytest

from evals.runner import Evaluation


@pytest.fixture(scope="session")
def evaluation():
    """Simulator, internal run endpoint, real gateway (needs `cd ../tool-gateway && uv sync`)."""
    with Evaluation() as running:
        yield running
