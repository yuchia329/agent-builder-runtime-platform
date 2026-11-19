"""The generated GitHub tools against a fake GitHub serving recorded responses."""

import pytest
from fake_github import REPO, FakeGitHub, fixture
from gateway_testing import CONTRACTS, claims

from gateway.adapters.token_store_memory import MemoryTokenStore
from gateway.auth.run_token import RunClaims
from gateway.config import CONNECTORS_FOLDER
from gateway.credentials import Credentials
from gateway.ports.token_store import CredentialKey
from gateway.tools.executor import ConnectorClient
from gateway.tools.openapi_to_mcp import load_connector
from gateway.tools.registry import CallContext, ToolFailure

TOKEN = "ghs_test_secret_value"
OWNER, NAME = REPO.split("/")
LABELS = f"/repos/{REPO}/issues/1/labels"
ISSUE = f"/repos/{REPO}/issues/7"
ENV = {"GITHUB_REPOS": f"{REPO},octocat/Other"}


@pytest.fixture
def github():
    with FakeGitHub() as server:
        server.on("GET", "/search/issues", fixture("search_issues"))
        server.on("GET", f"/repos/{REPO}/labels", fixture("labels"))
        server.on("POST", LABELS, fixture("add_labels"))
        server.on("GET", ISSUE, fixture("issue"))
        server.on("PATCH", ISSUE, fixture("issue_closed"))
        yield server


def client(server, store=None, env=ENV):
    store = store or bot_store()
    definition = load_connector(CONNECTORS_FOLDER / "github", CONTRACTS, env)
    return ConnectorClient(
        definition, Credentials(store), base_url=server.url, sleep=lambda _: None
    )


def bot_store(token=TOKEN):
    store = MemoryTokenStore()
    store.put(CredentialKey("tenant-test", "team-bot", "github"), {"token": token})
    return store


def call(server, tool, arguments, store=None, run=None):
    c = client(server, store)
    context = CallContext(run or RunClaims.from_payload(claims()), 3, "a" * 64)
    return c.call(c.connector.tools[tool], arguments, context)


def search(server, q="refund EUR"):
    return call(server, "github.search_issues", {"q": q})


def add_label(server, owner=OWNER, repo=NAME, labels=("bug",)):
    args = {"owner": owner, "repo": repo, "issue_number": 1, "labels": list(labels)}
    return call(server, "github.add_label", args)


def test_requests_carry_the_connector_headers_and_the_credential(github):
    search(github)

    headers = {k.lower(): v for k, v in github.requests[0].headers.items()}
    assert headers["authorization"] == f"Bearer {TOKEN}"
    assert headers["accept"] == "application/vnd.github+json"
    assert headers["x-github-api-version"] == "2022-11-28"


def test_search_is_scoped_to_the_allowed_repos(github):
    search(github, "refund OR refunds")

    assert github.requests[0].query["q"] == [
        "refund OR refunds is:issue repo:octocat/Hello-World repo:octocat/Other"
    ]
    assert github.requests[0].query["per_page"] == ["10"]


@pytest.mark.parametrize("q", ["x repo:other/secret", "org:acme crash", "USER:me", "-owner:x a"])
def test_search_refuses_scope_qualifiers_before_any_request(github, q):
    with pytest.raises(ToolFailure, match="repositories are added for you"):
        search(github, q)
    assert github.requests == []


def test_search_result_keeps_only_the_listed_fields(github):
    result = search(github)

    recorded = fixture("search_issues")["items"]
    assert len(result["items"]) == len(recorded)
    first = result["items"][0]
    assert set(first) == {"number", "title", "state", "labels", "url"}
    assert first["url"] == recorded[0]["html_url"]
    assert first["labels"] == [label["name"] for label in recorded[0]["labels"]]
    assert "body" not in str(result) and "repository_url" not in str(result)


def test_add_label_sends_githubs_own_body_and_lists_label_names(github):
    result = add_label(github)

    request = github.requests[-1]  # after the hook listed the labels
    assert (request.method, request.path) == ("POST", LABELS)
    assert request.body == {"labels": ["bug"]}
    assert result == {"items": [{"name": label["name"]} for label in fixture("add_labels")]}


@pytest.mark.parametrize(("owner", "repo"), [("octocat", "Secret"), ("evil", "Hello-World")])
def test_other_repos_are_refused_before_any_request(github, owner, repo):
    with pytest.raises(ToolFailure, match=f"'{owner}/{repo}' is not allowed"):
        add_label(github, owner, repo)
    assert github.requests == []


def test_repo_check_ignores_case(github):
    github.on("GET", "/repos/OctoCat/hello-world/labels", fixture("labels"))
    github.on("POST", "/repos/OctoCat/hello-world/issues/1/labels", fixture("add_labels"))
    add_label(github, "OctoCat", "hello-world")

    assert github.requests[-1].path == "/repos/OctoCat/hello-world/issues/1/labels"


def test_close_sends_the_fixed_state_and_the_reason(github):
    args = {"owner": OWNER, "repo": NAME, "issue_number": 7, "state_reason": "completed"}
    result = call(github, "github.close_issue", args)

    request = github.requests[-1]  # after the hook read the issue
    assert (request.method, request.path) == ("PATCH", ISSUE)
    assert request.body == {"state": "closed", "state_reason": "completed"}
    recorded = fixture("issue_closed")
    assert result == {
        "issue_number": recorded["number"],
        "state": "closed",
        "state_reason": recorded["state_reason"],
        "already_closed": False,
        "url": recorded["html_url"],
        "duplicate_of": None,
    }


def test_missing_credential_is_a_tool_failure_naming_what_is_missing(github):
    with pytest.raises(
        ToolFailure, match="No github credential for team-bot in tenant tenant-test"
    ):
        call(github, "github.search_issues", {"q": "x"}, store=MemoryTokenStore())
    assert github.requests == []


def test_owner_runs_use_the_owners_credential(github):
    store = MemoryTokenStore()
    store.put(CredentialKey("tenant-test", "user:42", "github"), {"token": "gho_owner"})
    run = RunClaims.from_payload(claims(acts_as="owner", owner="user:42"))

    call(github, "github.search_issues", {"q": "x"}, store=store, run=run)

    headers = {k.lower(): v for k, v in github.requests[0].headers.items()}
    assert headers["authorization"] == "Bearer gho_owner"


def test_server_errors_are_retried(github):
    github.fail_next(502, times=2)

    assert search(github)["items"]
    assert len(github.requests) == 3


def test_gives_up_after_three_attempts(github):
    github.fail_next(503, times=3)

    with pytest.raises(ToolFailure, match="github is unavailable"):
        search(github)


@pytest.mark.parametrize(
    ("status", "headers"),
    [(403, {"x-ratelimit-remaining": "0"}), (429, {})],
    ids=["primary", "secondary"],
)
def test_rate_limit_is_reported_not_retried(github, status, headers):
    github.fail_next(status, headers=headers)

    with pytest.raises(ToolFailure, match="rate limit"):
        search(github)
    assert len(github.requests) == 1


def test_client_errors_reach_the_model_without_the_token(github):
    github.on("POST", LABELS, {"message": "Validation Failed"}, status=422)

    with pytest.raises(ToolFailure, match="github answered 422: Validation Failed") as e:
        add_label(github)
    assert TOKEN not in str(e.value)


def test_unreachable_api_is_a_tool_failure():
    with FakeGitHub() as server:
        url = server.url
    definition = load_connector(CONNECTORS_FOLDER / "github", CONTRACTS, ENV)
    dead = ConnectorClient(definition, Credentials(bot_store()), base_url=url, sleep=lambda _: None)
    context = CallContext(RunClaims.from_payload(claims()), 3, "a" * 64)

    with pytest.raises(ToolFailure, match="unreachable"):
        dead.call(definition.tools["github.search_issues"], {"q": "x"}, context)
