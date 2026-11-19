import pytest
from gateway_testing import Keys

from gateway.__main__ import build
from gateway.config import Settings


def test_public_key_is_required():
    with pytest.raises(ValueError, match="RUN_TOKEN_PUBLIC_KEY"):
        Settings.from_env({})


def test_defaults_and_connector_list():
    settings = Settings.from_env({"RUN_TOKEN_PUBLIC_KEY": "k", "CONNECTORS": " fake, ,github"})

    assert settings.connectors == ("fake", "github")
    assert (settings.host, settings.port, settings.token_store) == ("127.0.0.1", 8090, "memory")


def test_only_the_memory_store_exists_yet():
    with pytest.raises(ValueError, match="TOKEN_STORE"):
        Settings.from_env({"RUN_TOKEN_PUBLIC_KEY": "k", "TOKEN_STORE": "ssm"})


def test_unknown_connector_fails_startup():
    settings = Settings.from_env(
        {"RUN_TOKEN_PUBLIC_KEY": Keys().public_pem, "CONNECTORS": "notion"}
    )

    with pytest.raises(ValueError, match="notion"):
        build(settings)
