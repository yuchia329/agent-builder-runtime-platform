"""GitHubTools against a fake GitHub serving recorded (cleaned) responses."""

import pytest
from fake_github_server import REPO, FakeGitHubServer, fixture

from worker.adapters.tools_local import GitHubTools
from worker.core.errors import ToolError

TOKEN = "github_pat_test_secret"
LABELS_PATH = f"/repos/{REPO}/issues/1/labels"


@pytest.fixture
def github():
    with FakeGitHubServer() as server:
        server.on("GET", "/search/issues", fixture("search_issues"))
        server.on("GET", f"/repos/{REPO}/labels/bug", fixture("label"))
        server.on("GET", f"/repos/{REPO}/labels", fixture("labels"))
        server.on("POST", LABELS_PATH, fixture("add_labels"))
        yield server


def tools(server, **kwargs):
    return GitHubTools(TOKEN, [REPO], api_url=server.url, sleep=lambda _: None, **kwargs)


def search(server, query="refund", repo=REPO):
    return tools(server).call("github.search_issues", {"repo": repo, "query": query})


def add_label(server, label="bug", repo=REPO):
    return tools(server).call("github.add_label", {"repo": repo, "issue_number": 1, "label": label})


def test_sends_token_and_api_headers(github):
    search(github)

    headers = github.requests[0].headers
    assert headers["Authorization"] == f"Bearer {TOKEN}"
    assert headers["Accept"] == "application/vnd.github+json"
    assert headers["X-GitHub-Api-Version"] == "2022-11-28"


def test_search_scopes_query_to_the_repo_and_returns_summaries(github):
    result = search(github, "refund EUR in:title")

    assert github.requests[0].query["q"] == [f"refund EUR in:title repo:{REPO} is:issue"]
    assert github.requests[0].query["per_page"] == ["10"]
    assert result["items"], "recorded search has matches"
    for item in result["items"]:
        assert set(item) == {"number", "title", "state", "labels", "url"}
        assert isinstance(item["labels"], list)


def test_search_drops_results_from_other_repos(github):
    recorded = fixture("search_issues")
    foreign = dict(recorded["items"][0], repository_url="https://api.github.com/repos/other/secret")
    github.on("GET", "/search/issues", {**recorded, "items": [*recorded["items"], foreign]})

    result = search(github)

    assert len(result["items"]) == len(recorded["items"])


@pytest.mark.parametrize(
    "query", ["refund repo:other/secret", "org:acme crash", "-user:x bug", "OWNER:me"]
)
def test_search_refuses_scope_qualifiers(github, query):
    with pytest.raises(ToolError, match="repository is added for you"):
        search(github, query)
    assert github.requests == []


def test_add_existing_label(github):
    result = add_label(github)

    assert github.requests[-1].body == {"labels": ["bug"]}
    assert result["issue_number"] == 1
    assert "bug" in result["labels"]


def test_add_unknown_label_lists_existing_ones(github):
    with pytest.raises(ToolError, match="'urgent' does not exist") as e:
        add_label(github, "urgent")

    for label in fixture("labels"):
        assert label["name"] in str(e.value)
    assert not any(r.method == "POST" for r in github.requests)


@pytest.mark.parametrize("call", [search, add_label])
def test_other_repos_are_refused(github, call):
    with pytest.raises(ToolError, match="'other/secret' is not allowed"):
        call(github, repo="other/secret")
    assert github.requests == []


def test_repo_match_ignores_case(github):
    search(github, repo=REPO.upper())
    assert REPO in github.requests[0].query["q"][0]


def test_server_errors_are_retried(github):
    github.fail_next(502, times=2)

    assert search(github)["items"]
    assert len(github.requests) == 3


def test_gives_up_after_retries(github):
    github.fail_next(503, times=3)

    with pytest.raises(ToolError, match="GitHub is unavailable"):
        search(github)


@pytest.mark.parametrize(
    ("status", "headers"),
    [(403, {"x-ratelimit-remaining": "0"}), (429, {})],
    ids=["primary", "secondary"],
)
def test_rate_limit_is_reported_not_retried(github, status, headers):
    github.fail_next(status, headers=headers)

    with pytest.raises(ToolError, match="rate limit"):
        search(github)
    assert len(github.requests) == 1


def test_client_error_message_reaches_the_model_without_the_token(github):
    github.on("POST", LABELS_PATH, {"message": "Validation Failed"}, status=422)

    with pytest.raises(ToolError, match="422: Validation Failed") as e:
        add_label(github)
    assert TOKEN not in str(e.value)


def test_unknown_tool(github):
    with pytest.raises(ToolError):
        tools(github).describe(["github.close_issue"])


def test_tools_say_whether_a_repeat_is_safe():
    with FakeGitHubServer() as server:
        search, label = tools(server).describe(["github.search_issues", "github.add_label"])

    assert search.read_only and search.safe_to_repeat
    assert label.idempotent and not label.read_only  # adding an existing label changes nothing
