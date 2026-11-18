"""GitHubTools against a fake GitHub serving recorded (cleaned) responses."""

import pytest
from fake_github_server import REPO, FakeGitHubServer, fixture

from worker.adapters.tools_local import GitHubTools
from worker.core.errors import ToolError

TOKEN = "github_pat_test_secret"
LABELS_PATH = f"/repos/{REPO}/issues/1/labels"
ISSUE_PATH = f"/repos/{REPO}/issues/7"  # recorded: an open issue, closed as duplicate of #1
ORIGINAL_PATH = f"/repos/{REPO}/issues/1"


@pytest.fixture
def github():
    with FakeGitHubServer() as server:
        server.on("GET", "/search/issues", fixture("search_issues"))
        server.on("GET", f"/repos/{REPO}/labels/bug", fixture("label"))
        server.on("GET", f"/repos/{REPO}/labels", fixture("labels"))
        server.on("POST", LABELS_PATH, fixture("add_labels"))
        server.on("GET", ISSUE_PATH, fixture("issue"))
        server.on("GET", ORIGINAL_PATH, fixture("issue_original"))
        server.on("PATCH", ISSUE_PATH, fixture("issue_closed"))
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
        tools(github).describe(["github.delete_repo"])
    with pytest.raises(ToolError, match="Unknown tool"):
        tools(github).call("github.delete_repo", {"repo": REPO})


def test_tools_say_whether_a_repeat_is_safe():
    with FakeGitHubServer() as server:
        search, label, close = tools(server).describe(
            ["github.search_issues", "github.add_label", "github.close_issue"]
        )

    assert search.read_only and search.safe_to_repeat
    assert label.idempotent and not label.read_only  # adding an existing label changes nothing
    assert close.idempotent and not close.read_only  # a closed issue is reported, not re-closed


# --- close_issue ------------------------------------------------------------------------------


def close(server, reason="duplicate", repo=REPO, number=7, **extra):
    arguments = {"repo": repo, "issue_number": number, "reason": reason} | extra
    return tools(server).call("github.close_issue", arguments)


def patches(server):
    return [r for r in server.requests if r.method == "PATCH"]


def test_close_as_duplicate_links_the_original_by_id(github):
    result = close(github, duplicate_of=1)

    assert result == {
        "issue_number": 7,
        "state": "closed",
        "state_reason": "duplicate",
        "already_closed": False,
        "url": "https://github.com/octocat/Hello-World/issues/7",
        "duplicate_of": 1,
    }
    [patch] = patches(github)
    assert patch.path == ISSUE_PATH
    assert patch.body == {
        "state": "closed",
        "state_reason": "duplicate",
        "duplicate_issue_id": fixture("issue_original")["id"],  # id, not the #number
    }


@pytest.mark.parametrize("reason", ["completed", "not_planned"])
def test_close_with_other_reasons_sends_only_state_and_reason(github, reason):
    close(github, reason)

    assert patches(github)[0].body == {"state": "closed", "state_reason": reason}


def test_already_closed_issue_is_reported_not_closed_again(github):
    # Recorded: a second PATCH on a closed issue answers 200 and overwrites its close reason.
    github.on("GET", ISSUE_PATH, fixture("issue_closed"))

    result = close(github, "completed")

    assert result["already_closed"] is True
    assert result["state_reason"] == "duplicate"  # unchanged
    assert patches(github) == []


def test_pull_request_is_refused(github):
    github.on("GET", ISSUE_PATH, fixture("issue") | {"pull_request": {"url": "x"}})

    with pytest.raises(ToolError, match="pull request"):
        close(github, "completed")
    assert patches(github) == []


def test_duplicate_of_a_pull_request_is_refused(github):
    github.on("GET", ORIGINAL_PATH, fixture("issue_original") | {"pull_request": {"url": "x"}})

    with pytest.raises(ToolError, match="pull request"):
        close(github, duplicate_of=1)
    assert patches(github) == []


@pytest.mark.parametrize(
    ("number", "extra", "message"),
    [
        (7, {"duplicate_of": 99}, "#99 does not exist"),
        (7, {"duplicate_of": 7}, "duplicate of itself"),
        (99, {}, "#99 does not exist"),
    ],
)
def test_missing_or_self_referencing_issues_are_refused(github, number, extra, message):
    with pytest.raises(ToolError, match=message):
        close(github, number=number, **extra)
    assert patches(github) == []


def test_duplicate_of_needs_reason_duplicate(github):
    with pytest.raises(ToolError, match="only allowed with reason 'duplicate'"):
        close(github, "completed", duplicate_of=1)
    assert github.requests == []


def test_unknown_reason_is_refused(github):
    with pytest.raises(ToolError, match="reason must be one of"):
        close(github, "spam")
    assert github.requests == []


def test_close_in_another_repo_is_refused(github):
    with pytest.raises(ToolError, match="not allowed"):
        close(github, "completed", repo="octocat/other")
    assert github.requests == []


def test_close_retries_server_errors(github):
    github.fail_next(502, times=2)

    assert close(github, "completed")["state"] == "closed"


def test_close_result_has_no_body_or_token(github):
    result = close(github, duplicate_of=1)

    assert set(result) == {
        "issue_number",
        "state",
        "state_reason",
        "already_closed",
        "url",
        "duplicate_of",
    }
    assert TOKEN not in str(result)
