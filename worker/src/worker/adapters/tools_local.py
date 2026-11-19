"""GitHub tools called directly from the worker with a personal token (M1 stage of ADR 0006).

In M4 these calls move into the tool gateway, which holds credentials; until then this adapter
holds the token, so it also enforces the limits a gateway would:

- Only repos in ``GITHUB_REPOS`` can be touched; any other ``repo`` argument is refused.
- Search stays inside the given repo: queries with ``repo:``, ``org:``, ``user:`` or ``owner:``
  qualifiers are refused, and results from other repos are dropped.
- Search returns small summaries (number, title, state, labels, URL), never issue bodies: less
  context, and less text written by outsiders for the model to read.
- ``add_label`` only adds labels that already exist in the repo (GitHub would create any new
  name the model invents); the error lists the existing labels so the model can pick one.
- ``close_issue`` reads the issue first: a pull request is refused (the issues endpoint would
  close it), and an already closed issue is left as it is (a second PATCH would overwrite its
  close reason, so the tool is idempotent only because it checks). ``duplicate_of`` must be
  another issue in the same repo; it is linked by GitHub's issue id. No comment is posted.

Errors become ToolError with GitHub's message (shown to the model; never the token). 5xx and
network errors are retried with backoff; rate limits are reported, not waited out.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable, Iterable, Sequence
from typing import Any
from urllib.parse import quote

import httpx

from worker.core.errors import ToolError
from worker.core.types import ToolContext, ToolSpec

log = logging.getLogger(__name__)

RETRY_STATUSES = frozenset({500, 502, 503, 504})
SEARCH_LIMIT = 10
REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
CLOSE_REASONS = ("completed", "not_planned", "duplicate")
SCOPE_QUALIFIER = re.compile(r"(?:^|\s)-?(repo|org|user|owner):", re.IGNORECASE)

_REPO_PROPERTY = {
    "type": "string",
    "pattern": REPO.pattern,
    "description": "Repository as owner/name.",
}

SPECS = {
    "github.search_issues": ToolSpec(
        "github.search_issues",
        "Search issues in a repository. Returns up to 10 matches with number, title, state, "
        "labels and URL (no bodies). Use GitHub search syntax, for example 'refund EUR in:title' "
        "or 'is:open label:bug'; the repository is added for you.",
        {
            "type": "object",
            "properties": {
                "repo": _REPO_PROPERTY,
                "query": {"type": "string", "minLength": 1, "maxLength": 256},
            },
            "required": ["repo", "query"],
            "additionalProperties": False,
        },
        read_only=True,
    ),
    "github.add_label": ToolSpec(
        "github.add_label",
        "Add an existing label to an issue. If the label does not exist, the error lists the "
        "repository's labels.",
        {
            "type": "object",
            "properties": {
                "repo": _REPO_PROPERTY,
                "issue_number": {"type": "integer", "minimum": 1},
                "label": {"type": "string", "minLength": 1, "maxLength": 50},
            },
            "required": ["repo", "issue_number", "label"],
            "additionalProperties": False,
        },
        idempotent=True,  # adding a label the issue already has changes nothing
    ),
    "github.close_issue": ToolSpec(
        "github.close_issue",
        "Close an issue. reason: 'completed' (fixed or done), 'not_planned' (won't do) or "
        "'duplicate' (give duplicate_of, the number of the original issue in the same "
        "repository). Closing an issue that is already closed changes nothing.",
        {
            "type": "object",
            "properties": {
                "repo": _REPO_PROPERTY,
                "issue_number": {"type": "integer", "minimum": 1},
                "reason": {"type": "string", "enum": list(CLOSE_REASONS)},
                "duplicate_of": {"type": "integer", "minimum": 1},
            },
            "required": ["repo", "issue_number", "reason"],
            "additionalProperties": False,
        },
        idempotent=True,  # an already closed issue is reported, never closed again
    ),
}


class GitHubTools:
    def __init__(
        self,
        token: str,
        repos: Iterable[str],
        *,
        api_url: str = "https://api.github.com",
        attempts: int = 3,
        backoff_seconds: float = 0.5,
        timeout_seconds: float = 15.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._api = api_url.rstrip("/")
        self._repos = {r.lower(): r for r in repos}
        self._attempts = attempts
        self._backoff = backoff_seconds
        self._sleep = sleep
        self._http = httpx.Client(
            timeout=timeout_seconds,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "agent-platform-worker",
            },
        )

    def describe(self, names: Sequence[str], context: ToolContext | None = None) -> list[ToolSpec]:
        missing = [n for n in names if n not in SPECS]
        if missing:
            raise ToolError(f"Unknown tools: {', '.join(missing)}")
        return [SPECS[n] for n in names]

    def call(self, name: str, arguments: dict[str, Any], context: ToolContext | None = None) -> Any:
        if name == "github.search_issues":
            return self._search_issues(self._repo(arguments["repo"]), arguments["query"])
        if name == "github.add_label":
            repo = self._repo(arguments["repo"])
            return self._add_label(repo, int(arguments["issue_number"]), arguments["label"])
        if name == "github.close_issue":
            repo = self._repo(arguments["repo"])
            duplicate_of = arguments.get("duplicate_of")
            return self._close_issue(
                repo,
                int(arguments["issue_number"]),
                arguments["reason"],
                None if duplicate_of is None else int(duplicate_of),
            )
        raise ToolError(f"Unknown tool: {name}")

    # --- tools ---------------------------------------------------------------------------------

    def _search_issues(self, repo: str, query: str) -> dict[str, Any]:
        if SCOPE_QUALIFIER.search(query):
            raise ToolError(
                "Do not use repo:, org:, user: or owner: in the query; the repository is "
                "added for you"
            )
        data = self._request(
            "GET",
            "/search/issues",
            params={"q": f"{query} repo:{repo} is:issue", "per_page": SEARCH_LIMIT},
        )
        own = f"/repos/{repo}".lower()
        items = [
            {
                "number": item["number"],
                "title": item["title"],
                "state": item["state"],
                "labels": [label["name"] for label in item.get("labels", [])],
                "url": item["html_url"],
            }
            for item in data.get("items", [])
            if item.get("repository_url", "").lower().endswith(own)
        ]
        return {"total_count": data.get("total_count", len(items)), "items": items}

    def _add_label(self, repo: str, issue_number: int, label: str) -> dict[str, Any]:
        response = self._send("GET", f"/repos/{repo}/labels/{quote(label, safe='')}")
        if response.status_code == 404:
            existing = self._request("GET", f"/repos/{repo}/labels", params={"per_page": 100})
            names = ", ".join(item["name"] for item in existing) or "none"
            raise ToolError(f"Label '{label}' does not exist in {repo}. Existing labels: {names}")
        self._check(response)
        labels = self._request(
            "POST", f"/repos/{repo}/issues/{issue_number}/labels", json={"labels": [label]}
        )
        return {"issue_number": issue_number, "labels": [item["name"] for item in labels]}

    def _close_issue(
        self, repo: str, number: int, reason: str, duplicate_of: int | None
    ) -> dict[str, Any]:
        if reason not in CLOSE_REASONS:
            raise ToolError(f"reason must be one of: {', '.join(CLOSE_REASONS)}")
        if duplicate_of is not None and reason != "duplicate":
            raise ToolError("duplicate_of is only allowed with reason 'duplicate'")
        if duplicate_of == number:
            raise ToolError("An issue cannot be a duplicate of itself")
        issue = self._issue(repo, number)
        if issue["state"] == "closed":
            return _closed(issue, already_closed=True)
        body: dict[str, Any] = {"state": "closed", "state_reason": reason}
        if duplicate_of is not None:
            body["duplicate_issue_id"] = self._issue(repo, duplicate_of)["id"]
        closed = self._request("PATCH", f"/repos/{repo}/issues/{number}", json=body)
        return _closed(closed, already_closed=False) | (
            {"duplicate_of": duplicate_of} if duplicate_of is not None else {}
        )

    def _issue(self, repo: str, number: int) -> dict[str, Any]:
        """An issue of the repo; pull requests (also served by /issues) are refused."""
        response = self._send("GET", f"/repos/{repo}/issues/{number}")
        if response.status_code in (404, 410):
            raise ToolError(f"Issue #{number} does not exist in {repo}")
        self._check(response)
        issue = response.json()
        if "pull_request" in issue:
            raise ToolError(f"#{number} in {repo} is a pull request, not an issue")
        return issue

    # --- HTTP ----------------------------------------------------------------------------------

    def _repo(self, repo: str) -> str:
        allowed = self._repos.get(repo.lower())
        if allowed is None:
            raise ToolError(
                f"Repository '{repo}' is not allowed. Allowed: {', '.join(self._repos.values())}"
            )
        return allowed

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self._send(method, path, **kwargs)
        self._check(response)
        return response.json()

    def _send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        problem = ""
        for attempt in range(1, self._attempts + 1):
            try:
                response = self._http.request(method, self._api + path, **kwargs)
            except httpx.TransportError as e:
                problem = f"{type(e).__name__}"
            else:
                if response.status_code not in RETRY_STATUSES:
                    return response
                problem = f"HTTP {response.status_code}"
            if attempt < self._attempts:
                log.warning("GitHub %s %s failed (%s); retrying", method, path, problem)
                self._sleep(self._backoff * 2 ** (attempt - 1))
        raise ToolError(f"GitHub is unavailable ({problem}); try again later")

    @staticmethod
    def _check(response: httpx.Response) -> None:
        if 200 <= response.status_code < 300:
            return
        message = _message(response)
        rate_limited = response.status_code == 429 or (
            response.status_code == 403
            and (
                response.headers.get("x-ratelimit-remaining") == "0"
                or "rate limit" in message.lower()
            )
        )
        if rate_limited:
            raise ToolError("GitHub rate limit reached; try again later")
        raise ToolError(f"GitHub returned {response.status_code}: {message}")


def _closed(issue: dict[str, Any], *, already_closed: bool) -> dict[str, Any]:
    return {
        "issue_number": issue["number"],
        "state": issue["state"],
        "state_reason": issue.get("state_reason"),
        "already_closed": already_closed,
        "url": issue["html_url"],
    }


def _message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:300]
    message = body.get("message", "") if isinstance(body, dict) else ""
    errors = body.get("errors") if isinstance(body, dict) else None
    if errors:
        message += " " + "; ".join(
            e.get("message", str(e)) if isinstance(e, dict) else str(e) for e in errors
        )
    return message.strip()[:300]
