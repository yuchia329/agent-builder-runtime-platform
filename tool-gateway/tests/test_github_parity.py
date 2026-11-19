# ruff: noqa: E501 (the parity map below is a table, one line per worker test)
"""Parity with the worker's M1 GitHub tools: every case of worker/tests/unit/test_tools_local.py
has a passing gateway test (M4 task 5). Map, worker test -> gateway test:

test_sends_token_and_api_headers -> test_github_connector::test_requests_carry_the_connector_headers_and_the_credential
test_search_scopes_query_to_the_repo_and_returns_summaries -> test_github_connector::test_search_is_scoped_to_the_allowed_repos, ::test_search_result_keeps_only_the_listed_fields
test_search_drops_results_from_other_repos -> here
test_search_refuses_scope_qualifiers -> test_github_connector::test_search_refuses_scope_qualifiers_before_any_request
test_add_existing_label -> here
test_add_unknown_label_lists_existing_ones -> here
test_other_repos_are_refused[search] -> here (test_search_cannot_name_a_repository)
test_other_repos_are_refused[add_label] -> test_github_connector::test_other_repos_are_refused_before_any_request
test_repo_match_ignores_case -> test_github_connector::test_repo_check_ignores_case
test_server_errors_are_retried -> test_github_connector::test_server_errors_are_retried
test_gives_up_after_retries -> test_github_connector::test_gives_up_after_three_attempts
test_rate_limit_is_reported_not_retried -> test_github_connector::test_rate_limit_is_reported_not_retried
test_client_error_message_reaches_the_model_without_the_token -> test_github_connector::test_client_errors_reach_the_model_without_the_token
test_unknown_tool -> test_mcp::test_tool_not_in_the_token_is_a_protocol_error, ::test_tool_in_the_token_but_not_loaded_is_a_protocol_error
test_tools_say_whether_a_repeat_is_safe -> here
test_close_as_duplicate_links_the_original_by_id -> here
test_close_with_other_reasons_sends_only_state_and_reason -> here
test_already_closed_issue_is_reported_not_closed_again -> here
test_pull_request_is_refused -> here
test_duplicate_of_a_pull_request_is_refused -> here
test_missing_or_self_referencing_issues_are_refused -> here
test_duplicate_of_needs_reason_duplicate -> here
test_unknown_reason_is_refused -> here
test_close_in_another_repo_is_refused -> here
test_close_retries_server_errors -> here
test_close_result_has_no_body_or_token -> here
"""

import pytest
from fake_github import REPO, FakeGitHub, fixture
from gateway_testing import CONTRACTS, claims
from jsonschema import Draft202012Validator

from gateway.adapters.token_store_memory import MemoryTokenStore
from gateway.auth.run_token import RunClaims
from gateway.config import CONNECTORS_FOLDER
from gateway.credentials import Credentials
from gateway.ports.token_store import CredentialKey
from gateway.tools.executor import ConnectorClient
from gateway.tools.openapi_to_mcp import load_connector
from gateway.tools.registry import CallContext, ToolFailure

TOKEN = "ghs_parity_secret"
OWNER, NAME = REPO.split("/")
LABELS_PATH = f"/repos/{REPO}/issues/1/labels"
ISSUE_PATH = f"/repos/{REPO}/issues/7"  # recorded: an open issue, closed as duplicate of #1
ORIGINAL_PATH = f"/repos/{REPO}/issues/1"
RUN = RunClaims.from_payload(claims())


@pytest.fixture
def github():
    with FakeGitHub() as server:
        server.on("GET", "/search/issues", fixture("search_issues"))
        server.on("GET", f"/repos/{REPO}/labels", fixture("labels"))
        server.on("POST", LABELS_PATH, fixture("add_labels"))
        server.on("GET", ISSUE_PATH, fixture("issue"))
        server.on("GET", ORIGINAL_PATH, fixture("issue_original"))
        server.on("PATCH", ISSUE_PATH, fixture("issue_closed"))
        yield server


def connector():
    return load_connector(CONNECTORS_FOLDER / "github", CONTRACTS, {"GITHUB_REPOS": REPO})


def client(server):
    store = MemoryTokenStore()
    store.put(CredentialKey("tenant-test", "team-bot", "github"), {"token": TOKEN})
    return ConnectorClient(
        connector(), Credentials(store), base_url=server.url, sleep=lambda _: None
    )


def call(server, tool, arguments):
    c = client(server)
    return c.call(c.connector.tools[tool], arguments, CallContext(RUN, 3, "a" * 64))


def add_label(server, label="bug"):
    args = {"owner": OWNER, "repo": NAME, "issue_number": 1, "labels": [label]}
    return call(server, "github.add_label", args)


def close(server, reason="duplicate", number=7, repo=NAME, **extra):
    args = {"owner": OWNER, "repo": repo, "issue_number": number, "state_reason": reason} | extra
    return call(server, "github.close_issue", args)


def patches(server):
    return [r for r in server.requests if r.method == "PATCH"]


# --- search ------------------------------------------------------------------------------------


def test_search_drops_results_from_other_repos(github):
    recorded = fixture("search_issues")
    foreign = dict(recorded["items"][0], repository_url="https://api.github.com/repos/other/secret")
    github.on("GET", "/search/issues", {**recorded, "items": [*recorded["items"], foreign]})

    result = call(github, "github.search_issues", {"q": "refund"})

    assert len(result["items"]) == len(recorded["items"])


def test_search_cannot_name_a_repository(github):
    schema = connector().tools["github.search_issues"].input_schema

    assert set(schema["properties"]) == {"q"}  # the repositories come from GITHUB_REPOS only
    with pytest.raises(ToolFailure, match="repositories are added for you"):
        call(github, "github.search_issues", {"q": "refund repo:other/secret"})
    assert github.requests == []


# --- labels ------------------------------------------------------------------------------------


def test_add_existing_label(github):
    result = add_label(github)

    assert github.requests[-1].body == {"labels": ["bug"]}
    assert "bug" in [item["name"] for item in result["items"]]


def test_add_unknown_label_lists_existing_ones(github):
    with pytest.raises(ToolFailure, match="'urgent' does not exist") as e:
        add_label(github, "urgent")

    for label in fixture("labels"):
        assert label["name"] in str(e.value)
    assert not any(r.method == "POST" for r in github.requests)


@pytest.mark.parametrize(
    "labels",
    [[], ["bug", "question"], [{"name": "bug", "suggest": True}]],
    ids=["none", "two", "object"],
)
def test_exactly_one_label_name(github, labels):
    args = {"owner": OWNER, "repo": NAME, "issue_number": 1, "labels": labels}

    with pytest.raises(ToolFailure, match="label"):
        call(github, "github.add_label", args)
    assert not any(r.method == "POST" for r in github.requests)


def test_existing_labels_are_listed_in_the_description(github):
    c = client(github)
    tool = next(t for t in c.tools() if t.name == "github.add_label")

    extra = tool.describe(RUN)

    names = ", ".join(label["name"] for label in fixture("labels"))
    assert extra == f"Existing labels in {REPO}: {names}"


def test_tools_say_whether_a_repeat_is_safe():
    tools = connector().tools
    search, label, close_tool = (
        tools["github.search_issues"],
        tools["github.add_label"],
        tools["github.close_issue"],
    )

    assert search.read_only and search.idempotent
    assert label.idempotent and not label.read_only  # adding an existing label changes nothing
    assert close_tool.idempotent and not close_tool.read_only  # a closed issue is not re-closed


# --- close_issue -------------------------------------------------------------------------------


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

    with pytest.raises(ToolFailure, match="pull request"):
        close(github, "completed")
    assert patches(github) == []


def test_duplicate_of_a_pull_request_is_refused(github):
    github.on("GET", ORIGINAL_PATH, fixture("issue_original") | {"pull_request": {"url": "x"}})

    with pytest.raises(ToolFailure, match="pull request"):
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
    with pytest.raises(ToolFailure, match=message):
        close(github, number=number, **extra)
    assert patches(github) == []


def test_duplicate_of_needs_reason_duplicate(github):
    with pytest.raises(ToolFailure, match="only allowed with state_reason 'duplicate'"):
        close(github, "completed", duplicate_of=1)
    assert github.requests == []


@pytest.mark.parametrize("reason", ["spam", "reopened"])
def test_unknown_reason_is_refused(reason):
    # The gateway validates arguments against this schema before any tool runs (test_mcp).
    schema = connector().tools["github.close_issue"].input_schema
    args = {"owner": OWNER, "repo": NAME, "issue_number": 7, "state_reason": reason}

    assert not Draft202012Validator(schema).is_valid(args)


def test_close_without_a_reason_is_refused(github):
    with pytest.raises(ToolFailure, match="Give state_reason"):
        call(github, "github.close_issue", {"owner": OWNER, "repo": NAME, "issue_number": 7})
    assert github.requests == []


def test_close_in_another_repo_is_refused(github):
    with pytest.raises(ToolFailure, match="not allowed"):
        close(github, "completed", repo="other")
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
