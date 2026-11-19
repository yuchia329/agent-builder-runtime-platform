import pytest
from gateway_testing import Keys, LiveGateway

from gateway.connectors.fake import FakeConnector


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="session")
def keys():
    return Keys()


@pytest.fixture
def fake():
    return FakeConnector()


@pytest.fixture
def gateway(keys, fake):
    with LiveGateway(keys, fake) as live:
        yield live
