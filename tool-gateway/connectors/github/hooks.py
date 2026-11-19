"""GitHub hooks (ADR 0014): the checks the allowlist rules cannot express, carried over from the
worker's M1 tools (worker/src/worker/adapters/tools_local.py). Each gets a `HookCall` after the
rules ran: `arguments` (GitHub's names), `hook_arguments`, `send` (the tool's own request),
`operation` (only `hook_operations` from allowlist.yaml) and `settings`. A hook returns the API
response the allowlist's result fields are picked from. Messages go to the model: no secrets.
"""

from typing import Any

from gateway.tools.registry import ApiError, ToolFailure

LABELS_PAGE = 100  # GitHub's largest page; repos with more labels are checked on the first page


def _allowed(call) -> set[str]:
    return {repo.lower() for repo in call.settings["GITHUB_REPOS"]}


# --- search ------------------------------------------------------------------------------------


def search_results_in_allowed_repos(call) -> Any:
    """Second guard behind the `repo:` qualifiers the rules add: items from any other repository
    are dropped."""
    response = call.send(call.arguments)
    allowed = _allowed(call)
    items = [
        item
        for item in response.get("items", [])
        if item.get("repository_url", "").lower().rsplit("/repos/", 1)[-1] in allowed
    ]
    return {**response, "items": items}


# --- labels ------------------------------------------------------------------------------------


def _label_names(operation, owner: str, repo: str) -> list[str]:
    labels = operation(
        "issues/list-labels-for-repo", {"owner": owner, "repo": repo, "per_page": LABELS_PAGE}
    )
    return [label["name"] for label in labels]


def existing_labels_only(call) -> Any:
    """Exactly one label, by name, that already exists: GitHub's add-labels call silently creates a
    misspelled label, and its body also accepts label objects (suggestions, rationale)."""
    labels = call.arguments.get("labels")
    if not labels or not isinstance(labels, list) or len(labels) != 1:
        raise ToolFailure('Give exactly one label in labels, for example ["bug"]')
    label = labels[0]
    if not isinstance(label, str):
        raise ToolFailure("labels must hold a label name (a string)")
    owner, repo = call.arguments["owner"], call.arguments["repo"]
    names = _label_names(call.operation, owner, repo)
    if label not in names:
        listed = ", ".join(names) or "none"
        raise ToolFailure(
            f"Label '{label}' does not exist in {owner}/{repo}. Existing labels: {listed}"
        )
    return call.send(call.arguments)


def _describe_labels(call) -> str | None:
    """Listed at tools/list so the model picks an existing label from the start."""
    lines = []
    for full_name in call.settings["GITHUB_REPOS"]:
        owner, _, repo = full_name.partition("/")
        names = _label_names(call.operation, owner, repo)
        lines.append(f"Existing labels in {full_name}: {', '.join(names) or 'none'}")
    return "\n".join(lines) or None


existing_labels_only.describe = _describe_labels


# --- close -------------------------------------------------------------------------------------


def _issue(call, number: int) -> dict[str, Any]:
    """An issue of the repo; pull requests (GitHub serves them as issues too) are refused."""
    owner, repo = call.arguments["owner"], call.arguments["repo"]
    try:
        issue = call.operation("issues/get", {"owner": owner, "repo": repo, "issue_number": number})
    except ApiError as e:
        if e.status in (404, 410):
            raise ToolFailure(f"Issue #{number} does not exist in {owner}/{repo}") from None
        raise
    if "pull_request" in issue:
        raise ToolFailure(f"#{number} in {owner}/{repo} is a pull request, not an issue")
    return issue


def close_issue_checks(call) -> Any:
    """Reads the issue before closing: pull requests are refused, and an issue already closed is
    reported as it is, never closed again (a second close overwrites its reason). `duplicate_of`
    (an issue number, same repository) becomes GitHub's internal `duplicate_issue_id`."""
    number = call.arguments["issue_number"]
    reason = call.arguments.get("state_reason")
    duplicate_of = call.hook_arguments.get("duplicate_of")
    if duplicate_of is not None and reason != "duplicate":
        raise ToolFailure("duplicate_of is only allowed with state_reason 'duplicate'")
    if duplicate_of == number:
        raise ToolFailure("An issue cannot be a duplicate of itself")
    if reason is None:
        raise ToolFailure("Give state_reason: completed, not_planned or duplicate")

    issue = _issue(call, number)
    if issue["state"] == "closed":
        return {**issue, "already_closed": True, "duplicate_of": None}
    values = dict(call.arguments)
    if duplicate_of is not None:
        values["duplicate_issue_id"] = _issue(call, duplicate_of)["id"]
    closed = call.send(values)
    return {**closed, "already_closed": False, "duplicate_of": duplicate_of}


HOOKS = {
    "search_results_in_allowed_repos": search_results_in_allowed_repos,
    "existing_labels_only": existing_labels_only,
    "close_issue_checks": close_issue_checks,
}
