import pytest
from fake_github import TOKEN_PATH, FakeGitHub, app_store, fixture
from gateway_testing import CONTRACTS, Keys, LiveGateway, mcp_client, meta, token

from gateway.__main__ import build, connectors
from gateway.config import Settings, connector_base_url
from gateway.credentials import Credentials


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


GITHUB = {"CONNECTORS": "github", "GITHUB_REPOS": "octocat/Hello-World"}


@pytest.mark.parametrize(
    "value",
    [
        "http://127.0.0.1:9001/",
        "https://localhost:9001",
        "http://[::1]:9001",
    ],
)
def test_connector_base_url_override_accepts_this_machine(value):
    setting, url = connector_base_url("github", {"CONNECTOR_GITHUB_BASE_URL": f" {value} "})

    assert setting == "CONNECTOR_GITHUB_BASE_URL"
    assert url == value.rstrip("/")


def test_connector_base_url_override_is_optional():
    assert connector_base_url("github", {}) is None
    assert connector_base_url("github", {"CONNECTOR_GITHUB_BASE_URL": " "}) is None
    assert connector_base_url("github", {"CONNECTOR_SLACK_BASE_URL": "http://127.0.0.1"}) is None


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("https://api.example.com", "must point at this machine"),
        ("http://10.0.0.5:9001", "must point at this machine"),
        ("http://127.0.0.1.example.com", "must point at this machine"),
        ("ftp://127.0.0.1", "http:// or https://"),
        ("127.0.0.1:9001", "http:// or https://"),
        ("http://user:secret@127.0.0.1", "must not hold"),
        ("http://127.0.0.1/?x=1", "must not hold"),
        ("http://127.0.0.1:notaport", "not a valid URL"),
    ],
)
def test_connector_base_url_override_refuses_other_hosts_and_bad_urls(value, message):
    with pytest.raises(ValueError, match=message) as refused:
        connector_base_url("github", {"CONNECTOR_GITHUB_BASE_URL": value})

    assert "secret" not in str(refused.value)


def test_bad_connector_base_url_fails_startup():
    env = {"RUN_TOKEN_PUBLIC_KEY": Keys().public_pem, **GITHUB}
    env["CONNECTOR_GITHUB_BASE_URL"] = "https://api.example.com"

    with pytest.raises(ValueError, match="CONNECTOR_GITHUB_BASE_URL"):
        build(Settings.from_env(env))


def test_connector_without_override_keeps_the_allowlist_base_url(caplog):
    settings = Settings.from_env({"RUN_TOKEN_PUBLIC_KEY": Keys().public_pem, **GITHUB})

    with caplog.at_level("WARNING", logger="gateway"):
        [client] = connectors(settings, CONTRACTS, Credentials(app_store()))

    assert client._base_url == "https://api.github.com"
    assert "instead of" not in caplog.text


@pytest.mark.anyio
async def test_override_sends_api_calls_and_the_app_token_exchange_there(keys, caplog):
    with FakeGitHub() as github:
        github.on("GET", "/search/issues", fixture("search_issues"))
        env = {"RUN_TOKEN_PUBLIC_KEY": keys.public_pem, **GITHUB}
        env["CONNECTOR_GITHUB_BASE_URL"] = github.url
        with caplog.at_level("WARNING", logger="gateway"):
            [client] = connectors(Settings.from_env(env), CONTRACTS, Credentials(app_store()))
        with LiveGateway(keys, client) as gateway:
            async with mcp_client(gateway, token(keys)) as session:
                result = await session.call_tool(
                    "github.search_issues", {"q": "refund"}, meta=meta()
                )

    assert not result.is_error
    assert [r.path for r in github.token_requests] == [TOKEN_PATH]
    assert [r.path for r in github.requests if r.path == "/search/issues"] == ["/search/issues"]
    assert f"Connector github calls {github.url} instead of https://api.github.com" in caplog.text
