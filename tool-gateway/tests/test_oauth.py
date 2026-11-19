"""Per-user login for acts_as owner (ADR 0015) against a fake GitHub login server: the browser
flow through the real gateway, then owner calls with the stored token."""

import logging
from contextlib import closing
from urllib.parse import parse_qs, urlsplit

import httpx2
import pytest
from fake_github import REPO, FakeGitHub, fixture
from fake_github_oauth import (
    CLIENT_ID,
    CLIENT_SECRET,
    EXPIRES_IN,
    LOGIN,
    REFRESH_EXPIRES_IN,
    STATE_SECRET,
    FakeGitHubOAuth,
)
from gateway_testing import CONTRACTS, Keys, LiveGateway, claims

from gateway.adapters.token_store_memory import MemoryTokenStore
from gateway.auth.run_token import RunClaims
from gateway.config import CONNECTORS_FOLDER, OAuthSettings, Settings
from gateway.credentials import Credentials
from gateway.oauth.github import GitHubOAuth
from gateway.oauth.logins import LoginRefused, OAuthLogins
from gateway.oauth.routes import COOKIE, oauth_app
from gateway.oauth.signing import BadNote, Signer
from gateway.ports.token_store import CredentialKey
from gateway.tools.executor import ConnectorClient
from gateway.tools.openapi_to_mcp import load_connector
from gateway.tools.registry import CallContext, ToolFailure

TENANT, OWNER = "tenant-test", "api:tenant-test"
KEY = CredentialKey(TENANT, OWNER, "github")
NOW = 1_800_000_000.0
LABELS = f"/repos/{REPO}/issues/1/labels"
ADD = {"owner": "octocat", "repo": "Hello-World", "issue_number": 1, "labels": ["bug"]}


class Clock:
    def __init__(self, now: float = NOW) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def github_login():
    with FakeGitHubOAuth() as server:
        yield server


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def store():
    return MemoryTokenStore()


def make_logins(server, store, clock, public_url="http://gateway.test") -> OAuthLogins:
    provider = GitHubOAuth(
        CLIENT_ID, CLIENT_SECRET, web_url=server.url, api_url=server.url, clock=clock
    )
    return OAuthLogins(store, provider, Signer(STATE_SECRET, clock), public_url, clock)


@pytest.fixture
def live(github_login, store, clock):
    """The real gateway with login routes; `live.logins` makes links for it."""
    holder = {}

    def routes(url: str):
        holder["logins"] = make_logins(github_login, store, clock, url)
        return oauth_app({"github": holder["logins"]}, secure_cookie=False)

    with LiveGateway(Keys(), oauth=routes) as gateway:
        gateway.logins = holder["logins"]
        yield gateway


def browser() -> httpx2.Client:
    return httpx2.Client(follow_redirects=True, timeout=10)


def log_in(live) -> httpx2.Response:
    with browser() as b:
        return b.get(live.logins.login_url(TENANT, OWNER))


# The browser flow


def test_login_stores_the_users_tokens_and_login(live, github_login, store):
    page = log_in(live)

    assert page.status_code == 200
    assert f"Connected github account {LOGIN} for {OWNER}" in page.text
    value = store.get(KEY)
    assert value["login"] == LOGIN
    assert value["access_token"].startswith("ghu_test_access_")
    assert value["refresh_token"].startswith("ghr_test_refresh_")
    assert value["expires_at"] == int(NOW) + EXPIRES_IN
    assert value["refresh_token_expires_at"] == int(NOW) + REFRESH_EXPIRES_IN


def test_login_uses_state_and_pkce_s256(live, github_login):
    log_in(live)

    authorize = next(r for r in github_login.requests if r.path == "/login/oauth/authorize")
    assert authorize.query["client_id"] == CLIENT_ID
    assert authorize.query["redirect_uri"] == f"{live.url}/oauth/github/callback"
    assert authorize.query["code_challenge_method"] == "S256"
    assert len(authorize.query["code_challenge"]) == 43
    assert authorize.query["state"]
    (exchange,) = github_login.token_forms()
    assert len(exchange["code_verifier"]) == 43  # the fake checked it against the challenge
    assert exchange["redirect_uri"] == authorize.query["redirect_uri"]


def test_start_sets_a_private_cookie_and_the_callback_clears_it(live):
    with browser() as b:
        start = b.get(live.logins.login_url(TENANT, OWNER), follow_redirects=False)
        cookie = start.headers["set-cookie"].lower()
        finished = b.get(start.headers["location"])

    assert start.status_code == 302
    assert start.headers["cache-control"] == "no-store"
    for part in (f"{COOKIE}=", "httponly", "samesite=lax", "path=/oauth/github", "max-age=600"):
        assert part in cookie
    assert "secure" not in cookie  # plain http on localhost
    assert finished.headers["set-cookie"].startswith(f'{COOKIE}=""')


def test_forged_ticket_is_refused_before_github(live, github_login):
    url = live.logins.login_url(TENANT, OWNER)
    with browser() as b:
        page = b.get(url[:-2] + ("AA" if not url.endswith("AA") else "BB"))

    assert page.status_code == 400
    assert "not valid" in page.text
    assert github_login.requests == []


def test_ticket_signed_with_another_secret_is_refused(live, github_login, clock):
    other = Signer("another-secret-0123456789abcdef-0123", clock)
    ticket = other.sign("ticket", {"t": TENANT, "o": OWNER, "v": "github"}, 600)
    with browser() as b:
        page = b.get(f"{live.url}/oauth/github/start?ticket={ticket}")

    assert page.status_code == 400
    assert github_login.requests == []


def test_expired_ticket_is_refused(live, github_login, clock):
    url = live.logins.login_url(TENANT, OWNER)
    clock.now += 601
    with browser() as b:
        page = b.get(url)

    assert page.status_code == 400
    assert "expired" in page.text
    assert github_login.requests == []


def test_a_state_is_not_a_ticket(live, github_login):
    with browser() as b:
        start = b.get(live.logins.login_url(TENANT, OWNER), follow_redirects=False)
        state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
        page = b.get(f"{live.url}/oauth/github/start?ticket={state}")

    assert page.status_code == 400


def callback_query(live) -> tuple[httpx2.Client, str]:
    """A browser that went through start and the provider, stopped before the callback."""
    b = browser()
    start = b.get(live.logins.login_url(TENANT, OWNER), follow_redirects=False)
    provider = b.get(start.headers["location"], follow_redirects=False)
    return b, provider.headers["location"]


def test_callback_in_another_browser_is_refused(live, github_login, store):
    first, callback = callback_query(live)
    first.close()
    with browser() as other:  # no cookie: someone else's browser
        page = other.get(callback)

    assert page.status_code == 400
    assert "did not start in this browser" in page.text
    assert github_login.token_forms() == []
    assert store.get(KEY) is None


def test_callback_with_another_cookie_is_refused(live, github_login, store):
    b, callback = callback_query(live)
    with closing(b):
        b.cookies.set(COOKIE, "someone-elses-nonce", domain="127.0.0.1", path="/oauth/github")
        page = b.get(callback)

    assert page.status_code == 400
    assert github_login.token_forms() == []


def test_forged_state_is_refused(live, github_login, store):
    b, callback = callback_query(live)
    url = urlsplit(callback)
    query = parse_qs(url.query)
    forged = Signer("another-secret-0123456789abcdef-0123").sign(
        "state", {"t": TENANT, "o": "api:victim", "v": "github", "n": "x"}, 600
    )
    with closing(b):
        page = b.get(f"{live.url}{url.path}?code={query['code'][0]}&state={forged}")

    assert page.status_code == 400
    assert github_login.token_forms() == []


def test_expired_state_is_refused(live, github_login, clock):
    b, callback = callback_query(live)
    clock.now += 601
    with closing(b):
        page = b.get(callback)

    assert page.status_code == 400
    assert "expired" in page.text


def test_code_github_refuses_is_reported(live, github_login, store):
    b, callback = callback_query(live)
    github_login.codes.clear()  # used or expired code
    with closing(b):
        page = b.get(callback)

    assert page.status_code == 400
    assert "bad_verification_code" in page.text
    assert store.get(KEY) is None


def test_wrong_pkce_verifier_is_refused_by_github(github_login, store, clock):
    logins = make_logins(github_login, store, clock)
    url, nonce = logins.begin(logins.login_url(TENANT, OWNER).split("ticket=")[1])
    location = github_login.authorize({k: v[0] for k, v in parse_qs(urlsplit(url).query).items()})
    code = parse_qs(urlsplit(location).query)["code"][0]
    # Another secret derives another verifier from the same nonce.
    other = OAuthLogins(store, logins.provider, Signer("x" * 40, clock), "http://gateway.test")
    state = Signer("x" * 40, clock).sign(
        "state", {"t": TENANT, "o": OWNER, "v": "github", "n": nonce}, 600
    )

    with pytest.raises(LoginRefused, match="bad_verification_code"):
        other.finish(state, code, nonce)
    assert store.get(KEY) is None


def test_person_who_denies_connects_nothing(live, github_login, store):
    github_login.deny = True
    page = log_in(live)

    assert page.status_code == 400
    assert "access_denied" in page.text
    assert github_login.token_forms() == []
    assert store.get(KEY) is None


def test_github_down_during_callback_is_a_502(live, github_login, store):
    b, callback = callback_query(live)
    github_login.fail_token_next(502)
    with closing(b):
        page = b.get(callback)

    assert page.status_code == 502
    assert store.get(KEY) is None


def test_routes_answer_503_without_login_settings(gateway):
    response = httpx2.get(f"{gateway.url}/oauth/github/start?ticket=x")

    assert response.status_code == 503


def test_unknown_provider_is_404(live):
    assert httpx2.get(f"{live.url}/oauth/notion/start?ticket=x").status_code == 404


def test_pages_and_logs_hold_no_secret(live, github_login, store, caplog):
    with caplog.at_level(logging.DEBUG):
        pages = [log_in(live)]
        github_login.deny = True
        pages.append(log_in(live))
    secrets = [CLIENT_SECRET, STATE_SECRET, store.get(KEY)["access_token"]]
    secrets.append(store.get(KEY)["refresh_token"])
    seen = caplog.text + "".join(p.text for p in pages)

    for secret in secrets:
        assert secret not in seen
    assert CLIENT_SECRET not in repr(live.logins.provider)


# Owner calls


@pytest.fixture
def api():
    with FakeGitHub() as server:
        server.on("GET", f"/repos/{REPO}/labels", fixture("labels"))
        server.on("POST", LABELS, fixture("add_labels"))
        yield server


def owner_call(api, logins):
    definition = load_connector(CONNECTORS_FOLDER / "github", CONTRACTS, {"GITHUB_REPOS": REPO})
    credentials = Credentials(logins._store, logins={"github": logins} if logins else None)
    client = ConnectorClient(definition, credentials, base_url=api.url, sleep=lambda _: None)
    run = RunClaims.from_payload(claims(acts_as="owner", owner=OWNER, tenant=TENANT))
    context = CallContext(run, 3, "a" * 64)
    return client.call(client.connector.tools["github.add_label"], ADD, context)


def authorization(request) -> str:
    return {k.lower(): v for k, v in request.headers.items()}["authorization"]


def connected(github_login, store, clock) -> OAuthLogins:
    logins = make_logins(github_login, store, clock)
    value = github_login.issue()
    store.put(
        KEY,
        {
            "access_token": value["access_token"],
            "refresh_token": value["refresh_token"],
            "expires_at": int(clock()) + EXPIRES_IN,
            "refresh_token_expires_at": int(clock()) + REFRESH_EXPIRES_IN,
            "login": LOGIN,
        },
    )
    return logins


def test_owner_calls_use_the_users_token(api, github_login, store, clock):
    logins = connected(github_login, store, clock)

    owner_call(api, logins)

    assert authorization(api.requests[-1]) == f"Bearer {store.get(KEY)['access_token']}"
    assert github_login.token_forms() == []  # not due: no refresh


def test_owner_without_login_gets_a_working_login_link(api, github_login, store, clock):
    logins = make_logins(github_login, store, clock)

    with pytest.raises(ToolFailure) as failure:
        owner_call(api, logins)

    message = str(failure.value)
    assert f"{OWNER} has no connected account" in message
    assert "http://gateway.test/oauth/github/start?ticket=" in message
    ticket = message.split("ticket=")[1].split()[0]
    logins.begin(ticket)  # the link in the message starts a login
    assert api.requests == []


def test_token_is_refreshed_five_minutes_before_expiry(api, github_login, store, clock):
    logins = connected(github_login, store, clock)
    old = store.get(KEY)
    clock.now += EXPIRES_IN - 299

    owner_call(api, logins)

    (form,) = github_login.token_forms()
    assert form["grant_type"] == "refresh_token"
    assert form["refresh_token"] == old["refresh_token"]
    new = store.get(KEY)
    assert new["access_token"] != old["access_token"]
    assert new["refresh_token"] != old["refresh_token"]  # GitHub replaces it; the new one is kept
    assert new["login"] == LOGIN
    assert authorization(api.requests[-1]) == f"Bearer {new['access_token']}"


def test_api_401_refreshes_once_and_retries(api, github_login, store, clock):
    logins = connected(github_login, store, clock)
    api.fail_next(401)

    owner_call(api, logins)

    assert len(github_login.token_forms()) == 1
    assert authorization(api.requests[-1]) == f"Bearer {store.get(KEY)['access_token']}"


def test_refused_refresh_deletes_the_login_and_asks_for_a_new_one(api, github_login, store, clock):
    logins = connected(github_login, store, clock)
    github_login.revoke_all()  # the person revoked the App's access
    clock.now += EXPIRES_IN

    with pytest.raises(ToolFailure, match=f"{OWNER} must log in again.*start\\?ticket="):
        owner_call(api, logins)
    assert store.get(KEY) is None
    assert api.requests == []


def test_expired_refresh_token_asks_for_a_login_without_calling_github(
    api, github_login, store, clock
):
    logins = connected(github_login, store, clock)
    clock.now += REFRESH_EXPIRES_IN

    with pytest.raises(ToolFailure, match="must log in again"):
        owner_call(api, logins)
    assert github_login.token_forms() == []


def test_github_down_during_refresh_keeps_the_login(api, github_login, store, clock):
    logins = connected(github_login, store, clock)
    clock.now += EXPIRES_IN
    github_login.fail_token_next(503)

    with pytest.raises(ToolFailure, match="github login service is unavailable"):
        owner_call(api, logins)
    assert store.get(KEY) is not None


def test_token_that_never_expires_is_never_refreshed(api, github_login, store, clock):
    github_login.expires = False
    logins = make_logins(github_login, store, clock)
    value = github_login.issue()
    store.put(KEY, {"access_token": value["access_token"], "expires_at": None, "login": LOGIN})
    clock.now += 10 * EXPIRES_IN

    owner_call(api, logins)

    assert github_login.token_forms() == []


def test_owner_runs_without_login_settings_are_refused(api, store):
    definition = load_connector(CONNECTORS_FOLDER / "github", CONTRACTS, {"GITHUB_REPOS": REPO})
    client = ConnectorClient(definition, Credentials(store), base_url=api.url)
    run = RunClaims.from_payload(claims(acts_as="owner", owner=OWNER))

    with pytest.raises(ToolFailure, match="user login is not set up"):
        client.call(client.connector.tools["github.add_label"], ADD, CallContext(run, 3, "a" * 64))


def test_tool_errors_hold_no_token(api, github_login, store, clock):
    logins = connected(github_login, store, clock)
    value = store.get(KEY)
    github_login.revoke_all()
    clock.now += EXPIRES_IN

    with pytest.raises(ToolFailure) as failure:
        owner_call(api, logins)
    for secret in (value["access_token"], value["refresh_token"], CLIENT_SECRET, STATE_SECRET):
        assert secret not in str(failure.value)


# Signed notes


def test_note_for_another_purpose_or_tampered_is_refused():
    signer = Signer(STATE_SECRET, Clock())
    note = signer.sign("ticket", {"t": TENANT}, 600)

    assert signer.verify("ticket", note)["t"] == TENANT
    with pytest.raises(BadNote):
        signer.verify("state", note)
    data, mac = note.split(".")
    with pytest.raises(BadNote):
        signer.verify("ticket", f"{data}x.{mac}")
    with pytest.raises(BadNote):
        signer.verify("ticket", "garbage")


# Settings

LOGIN_ENV = {
    "RUN_TOKEN_PUBLIC_KEY": "k",
    "GITHUB_OAUTH_CLIENT_ID": CLIENT_ID,
    "GITHUB_OAUTH_CLIENT_SECRET": CLIENT_SECRET,
    "OAUTH_STATE_SECRET": STATE_SECRET,
}


def test_login_settings_from_the_environment():
    settings = Settings.from_env(LOGIN_ENV | {"GATEWAY_PUBLIC_URL": "https://gw.example/"})

    assert settings.oauth == OAuthSettings(
        CLIENT_ID, CLIENT_SECRET, STATE_SECRET, "https://gw.example"
    )
    assert Settings.from_env(LOGIN_ENV).oauth.public_url == "http://localhost:8090"
    assert Settings.from_env({"RUN_TOKEN_PUBLIC_KEY": "k"}).oauth is None


@pytest.mark.parametrize("missing", ["GITHUB_OAUTH_CLIENT_ID", "OAUTH_STATE_SECRET"])
def test_part_of_the_login_settings_names_the_rest(missing):
    env = {k: v for k, v in LOGIN_ENV.items() if k != missing}

    with pytest.raises(ValueError, match=missing):
        Settings.from_env(env)


def test_short_state_secret_is_refused():
    with pytest.raises(ValueError, match="at least 32"):
        Settings.from_env(LOGIN_ENV | {"OAUTH_STATE_SECRET": "short"})


def test_settings_repr_shows_no_secret():
    text = repr(Settings.from_env(LOGIN_ENV | {"GITHUB_TOKEN": "ghp_secret"}))

    for secret in (CLIENT_SECRET, STATE_SECRET, "ghp_secret"):
        assert secret not in text


def test_gateway_starts_with_login_routes():
    from gateway.__main__ import build, user_logins

    settings = Settings.from_env(LOGIN_ENV | {"RUN_TOKEN_PUBLIC_KEY": Keys().public_pem})

    assert build(settings) is not None
    logins = user_logins(settings, MemoryTokenStore())["github"]
    assert logins.redirect_uri == "http://localhost:8090/oauth/github/callback"


def test_request_log_hides_login_queries():
    from gateway.__main__ import HideLoginQueries

    def line(path: str) -> str:
        record = logging.LogRecord(
            "uvicorn.access",
            logging.INFO,
            "",
            0,
            '%s - "%s %s HTTP/%s" %d',
            ("127.0.0.1:1", "GET", path, "1.1", 200),
            None,
        )
        HideLoginQueries().filter(record)
        return record.getMessage()

    assert "secret-code" not in line("/oauth/github/callback?code=secret-code&state=s")
    assert "/oauth/github/callback?<hidden>" in line("/oauth/github/callback?code=x")
    assert "/health?x=1" in line("/health?x=1")
