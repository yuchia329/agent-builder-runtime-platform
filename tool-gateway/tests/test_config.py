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


def test_control_plane_url_is_optional():
    base = {"RUN_TOKEN_PUBLIC_KEY": "k"}

    assert Settings.from_env(base).control_plane_url == ""
    url = Settings.from_env({**base, "CONTROL_PLANE_URL": " http://cp:8080 "}).control_plane_url
    assert url == "http://cp:8080"


def test_startup_without_control_plane_url_warns_that_approval_tools_are_refused(caplog):
    settings = Settings.from_env({"RUN_TOKEN_PUBLIC_KEY": Keys().public_pem, "CONNECTORS": "fake"})

    with caplog.at_level("WARNING", logger="gateway"):
        build(settings)

    assert "tools that need approval will be refused" in caplog.text
