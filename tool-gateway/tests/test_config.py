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


def test_github_connector_loads_from_the_connectors_folder():
    env = {
        "RUN_TOKEN_PUBLIC_KEY": Keys().public_pem,
        "CONNECTORS": "fake,github",
        "GITHUB_REPOS": "octocat/Hello-World",
    }

    with pytest.raises(ValueError, match="defined twice"):  # fake and github share tool names
        build(Settings.from_env(env))

    env["CONNECTORS"] = "github"
    assert build(Settings.from_env(env)) is not None


def test_github_connector_without_repos_fails_startup():
    env = {"RUN_TOKEN_PUBLIC_KEY": Keys().public_pem, "CONNECTORS": "github"}

    with pytest.raises(ValueError, match="GITHUB_REPOS is not set"):
        build(Settings.from_env(env))
