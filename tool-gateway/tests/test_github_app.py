"""GitHub App credential for team-bot: app JWT, installation token exchange, cache, renewal, and
seeding from the environment. Against the fake GitHub (tests/fake_github.py)."""

import time
from datetime import UTC, datetime

import jwt
import pytest
from fake_github import (
    APP_ID,
    APP_KEY,
    APP_PEM,
    APP_TOKEN,
    INSTALLATION_ID,
    REPO,
    FakeGitHub,
    app_credential,
    fixture,
)
from gateway_testing import CONTRACTS, claims

from gateway.adapters.token_store_memory import MemoryTokenStore
from gateway.auth.run_token import RunClaims
from gateway.config import CONNECTORS_FOLDER
from gateway.credentials import Credentials
from gateway.github_app import GitHubAppTokens
from gateway.ports.token_store import CredentialKey
from gateway.seed import seed_from_env
from gateway.tools.executor import ConnectorClient
from gateway.tools.openapi_to_mcp import load_connector
from gateway.tools.registry import ApiError, CallContext, ToolFailure

KEY = CredentialKey("tenant-test", "team-bot", "github")
SEARCH = {"q": "refund"}


@pytest.fixture
def github():
    with FakeGitHub() as server:
        server.on("GET", "/search/issues", fixture("search_issues"))
        yield server


class Clock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def client(server, value=None, tokens=None):
    store = MemoryTokenStore()
    store.put(KEY, app_credential() if value is None else value)
    tokens = tokens or GitHubAppTokens(sleep=lambda _: None)
    definition = load_connector(CONNECTORS_FOLDER / "github", CONTRACTS, {"GITHUB_REPOS": REPO})
    return ConnectorClient(
        definition, Credentials(store, tokens), base_url=server.url, sleep=lambda _: None
    )


def search(c):
    context = CallContext(RunClaims.from_payload(claims()), 3, "a" * 64)
    return c.call(c.connector.tools["github.search_issues"], SEARCH, context)


def authorization(request) -> str:
    return {k.lower(): v for k, v in request.headers.items()}["authorization"]


def test_app_jwt_is_exchanged_for_an_installation_token(github):
    search(client(github))

    (exchange,) = github.token_requests
    assert exchange.path == f"/app/installations/{INSTALLATION_ID}/access_tokens"
    app_jwt = authorization(exchange).removeprefix("Bearer ")
    header = jwt.get_unverified_header(app_jwt)
    decoded = jwt.decode(app_jwt, APP_KEY.public_key(), algorithms=["RS256"])
    assert header["alg"] == "RS256"
    assert decoded["iss"] == APP_ID
    assert decoded["exp"] - decoded["iat"] == 600  # GitHub allows at most 10 minutes
    assert decoded["iat"] <= time.time() - 59  # backdated for clock drift
    assert authorization(github.requests[0]) == f"Bearer {APP_TOKEN}"


def test_installation_token_is_reused_across_calls(github):
    c = client(github)
    search(c)
    search(c)

    assert len(github.token_requests) == 1
    assert len(github.requests) == 2


def test_token_is_renewed_five_minutes_before_it_expires(github):
    expires = datetime(2030, 1, 1, tzinfo=UTC).timestamp()
    github.app_token_expires_at = "2030-01-01T00:00:00Z"
    clock = Clock(expires - 3600)
    c = client(github, tokens=GitHubAppTokens(clock=clock, sleep=lambda _: None))

    search(c)
    clock.now = expires - 301
    search(c)
    assert len(github.token_requests) == 1

    clock.now = expires - 299
    search(c)
    assert len(github.token_requests) == 2


def test_api_401_renews_the_token_once_and_retries(github):
    c = client(github)
    search(c)
    github.app_token = "ghs_second_token"
    github.fail_next(401)

    assert search(c)["items"]
    assert len(github.token_requests) == 2
    assert authorization(github.requests[-1]) == "Bearer ghs_second_token"


def test_api_401_after_renewal_reaches_the_model(github):
    github.fail_next(401, times=2)

    with pytest.raises(ApiError, match="github answered 401") as e:
        search(client(github))
    assert e.value.status == 401
    assert len(github.token_requests) == 2  # the first token and one renewal
    assert len(github.requests) == 2


@pytest.mark.parametrize("status", [401, 403, 404])
def test_rejected_exchange_is_a_tool_failure_without_secrets(github, status):
    github.fail_token_next(status)

    with pytest.raises(
        ToolFailure, match=f"rejected the App credential for team-bot \\({status}\\)"
    ) as e:
        search(client(github))
    assert github.requests == []  # nothing sent to the API without a token
    assert "PRIVATE KEY" not in str(e.value)
    assert APP_TOKEN not in str(e.value)


def test_exchange_server_errors_are_retried(github):
    github.fail_token_next(502, times=2)

    assert search(client(github))["items"]
    assert len(github.token_requests) == 3


def test_exchange_gives_up_after_three_attempts(github):
    github.fail_token_next(503, times=3)

    with pytest.raises(ToolFailure, match="token request failed \\(503\\)"):
        search(client(github))


@pytest.mark.parametrize("missing", ["app_id", "private_key", "installation_id"])
def test_stored_value_missing_a_field_names_it(github, missing):
    value = {k: v for k, v in app_credential().items() if k != missing}

    with pytest.raises(ToolFailure, match=f"missing {missing}"):
        search(client(github, value))
    assert github.token_requests == []


def test_unusable_private_key_is_a_tool_failure_without_the_key(github):
    value = {**app_credential(), "private_key": "-----BEGIN RSA PRIVATE KEY-----\nnot-a-key\n"}

    with pytest.raises(ToolFailure, match="private key cannot be used") as e:
        search(client(github, value))
    assert "not-a-key" not in str(e.value)
    assert e.value.__cause__ is None and e.value.__suppress_context__


def test_credential_repr_never_shows_the_key():
    from gateway.github_app import AppCredential

    credential = AppCredential.from_value(app_credential())
    assert "PRIVATE" not in repr(credential)
    assert APP_ID in repr(credential)


# Seeding the in-memory TokenStore from the environment (local runs).


@pytest.fixture
def key_file(tmp_path):
    path = tmp_path / "app.pem"
    path.write_text(APP_PEM)
    return path


def app_env(key_file, **overrides):
    env = {
        "TENANT_ID": "tenant-test",
        "GITHUB_APP_ID": APP_ID,
        "GITHUB_APP_INSTALLATION_ID": INSTALLATION_ID,
        "GITHUB_APP_PRIVATE_KEY_FILE": str(key_file),
    }
    return {k: v for k, v in {**env, **overrides}.items() if v is not None}


def test_seed_stores_the_app_credential(key_file):
    store = MemoryTokenStore()

    seeded = seed_from_env(store, app_env(key_file))

    assert store.get(KEY) == app_credential()
    assert seeded == [f"github team-bot for tenant-test: GitHub App {APP_ID}"]
    assert "PRIVATE" not in " ".join(seeded)


def test_seed_without_app_settings_stores_nothing():
    store = MemoryTokenStore()

    assert seed_from_env(store, {"TENANT_ID": "tenant-test"}) == []
    assert store.get(KEY) is None


@pytest.mark.parametrize(
    "missing",
    ["TENANT_ID", "GITHUB_APP_ID", "GITHUB_APP_INSTALLATION_ID", "GITHUB_APP_PRIVATE_KEY_FILE"],
)
def test_seed_with_part_of_the_settings_names_the_rest(key_file, missing):
    with pytest.raises(ValueError, match=f"incomplete: set {missing}"):
        seed_from_env(MemoryTokenStore(), app_env(key_file, **{missing: None}))


def test_seed_with_unreadable_key_file_fails(tmp_path):
    with pytest.raises(ValueError, match="cannot be read"):
        seed_from_env(MemoryTokenStore(), app_env(tmp_path / "missing.pem"))


def test_seed_with_a_file_that_is_not_a_key_fails(tmp_path):
    path = tmp_path / "app.pem"
    path.write_text("hello")

    with pytest.raises(ValueError, match="not a PEM private key"):
        seed_from_env(MemoryTokenStore(), app_env(path))
