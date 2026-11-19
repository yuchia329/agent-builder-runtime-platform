import httpx
import pytest

from evals.simulators.github import INSTALLATION_TOKEN, ISSUE_ID_BASE, REPO, GitHubSimulator

WORLD = {
    "labels": ["bug", "enhancement", "duplicate"],
    "issues": [
        {
            "number": 1,
            "title": "Crash when uploading a large PNG",
            "body": "App crashes.",
            "labels": ["bug"],
        },
        {"number": 2, "title": "PNG export colors", "body": "Colors look washed out."},
        {
            "number": 3,
            "title": "Uploads crash on Android",
            "state": "closed",
            "state_reason": "completed",
        },
        {"number": 4, "title": "Fix PNG crash", "pull_request": True},
    ],
}


@pytest.fixture(scope="module")
def sim():
    with GitHubSimulator() as simulator:
        yield simulator


@pytest.fixture
def github(sim):
    sim.load(WORLD)
    with httpx.Client(base_url=sim.url) as client:
        yield client


def search(github, q: str) -> list[int]:
    response = github.get(
        "/search/issues", params={"q": f"{q} repo:{REPO} is:issue", "per_page": 10}
    )
    assert response.status_code == 200
    return [item["number"] for item in response.json()["items"]]


@pytest.mark.parametrize(
    ("q", "found"),
    [
        ("png", [2, 1]),  # case-insensitive, newest first
        ("PNG crash", [1]),  # every word in the group
        ("crash OR colors", [3, 2, 1]),  # either group
        ("crashes", [1]),  # whole words: "crashes" is in the body of 1 only
        ('"large PNG"', [1]),  # phrase: consecutive words
        ('"PNG large"', []),
        ("PNG -crash", [2]),  # excluded word
        ("uploads", [3]),  # closed issues are found too
        ("", [3, 2, 1]),  # no words: every issue (not the pull request)
    ],
)
def test_search_matches_words_like_github(github, q, found):
    assert search(github, q) == found


def test_search_filters_and_scope(github):
    assert search(github, "crash is:open") == [1]
    assert search(github, "label:bug") == [1]
    q = {"q": "crash repo:other/repo is:issue"}
    assert github.get("/search/issues", params=q).json()["items"] == []
    assert github.get("/search/issues", params={"q": "crash"}).status_code == 422  # no repo:
    q = {"q": f"crash repo:{REPO} is:pr"}
    assert [i["number"] for i in github.get("/search/issues", params=q).json()["items"]] == [4]


def test_search_items_have_the_fields_the_connector_keeps(github):
    item = github.get("/search/issues", params={"q": f"PNG crash repo:{REPO} is:issue"}).json()[
        "items"
    ][0]

    assert item["repository_url"] == f"https://api.github.com/repos/{REPO}"
    assert item["html_url"] == f"https://github.com/{REPO}/issues/1"
    assert item["labels"][0]["name"] == "bug"
    assert item["state"] == "open"


def test_labels_and_issues_are_read_from_the_world(github):
    assert [label["name"] for label in github.get(f"/repos/{REPO}/labels").json()] == WORLD[
        "labels"
    ]
    assert github.get(f"/repos/{REPO}/issues/2").json()["title"] == "PNG export colors"
    assert "pull_request" in github.get(f"/repos/{REPO}/issues/4").json()
    assert github.get(f"/repos/{REPO}/issues/99").status_code == 404
    assert github.get("/repos/other/repo/issues/1").status_code == 404


def test_adding_a_label_changes_the_state(github, sim):
    response = github.post(f"/repos/{REPO}/issues/2/labels", json={"labels": ["bug"]})

    assert response.status_code == 200
    assert [label["name"] for label in response.json()] == ["bug"]
    assert sim.snapshot()["issues"][2]["labels"] == ["bug"]


def test_closing_as_duplicate_records_the_original(github, sim):
    body = {"state": "closed", "state_reason": "duplicate", "duplicate_issue_id": ISSUE_ID_BASE + 1}
    response = github.patch(f"/repos/{REPO}/issues/2", json=body)

    assert response.status_code == 200
    assert response.json()["state"] == "closed"
    issue = sim.snapshot()["issues"][2]
    assert (issue["state"], issue["state_reason"], issue["duplicate_of"]) == (
        "closed",
        "duplicate",
        1,
    )


def test_duplicate_of_an_unknown_issue_id_is_refused(github):
    body = {"state": "closed", "duplicate_issue_id": 12345}

    assert github.patch(f"/repos/{REPO}/issues/2", json=body).status_code == 422


def test_load_resets_the_state(github, sim):
    github.post(f"/repos/{REPO}/issues/2/labels", json={"labels": ["new-label"]})
    sim.load(WORLD)

    assert sim.snapshot()["labels"] == WORLD["labels"]
    assert sim.snapshot()["issues"][2]["labels"] == []


def test_app_token_exchange_answers_a_throwaway_token(github):
    response = github.post("/app/installations/1/access_tokens")

    assert response.status_code == 201
    assert response.json()["token"] == INSTALLATION_TOKEN
