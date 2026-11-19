"""A simulated GitHub for evals: one repository whose issues and labels come from a task's world.

It answers what the gateway's GitHub connector sends (tool-gateway/connectors/github/), with
response shapes copied from the cleaned recordings in tool-gateway/tests/fixtures/github/:

- POST /app/installations/{id}/access_tokens: a throwaway installation token;
- GET  /search/issues: words matched against title and body (see `matches`);
- GET  /repos/{owner}/{repo}/labels;
- GET  /repos/{owner}/{repo}/issues/{number};
- POST /repos/{owner}/{repo}/issues/{number}/labels: adds labels (like GitHub, an unknown name
  is created; the connector's hook refuses those before they get here);
- PATCH /repos/{owner}/{repo}/issues/{number}: state, state_reason, duplicate_issue_id.

Anything else answers 404 like GitHub. Labeling and closing change the state; `snapshot()` is
the end state the checks read. Loopback only, one task at a time (`load` resets).

Search is a model of GitHub's, not a copy: terms are whole words, case-insensitive, all required
within a group; `OR` separates groups; "quoted phrases" match as consecutive words; `-word`
excludes. Qualifiers: `repo:` (must be this repository), `is:issue` / `is:pr`, `is:open` /
`is:closed`, `state:`, `label:`; other qualifiers are ignored. GitHub would also match other
forms of a word in a few cases (stemming); the spec tells the model not to rely on that.
"""

from __future__ import annotations

import copy
import json
import re
import shlex
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

REPO = "octocat/Hello-World"
API = "https://api.github.com"
WEB = "https://github.com"
INSTALLATION_TOKEN = "ghs_eval_simulated"  # not a secret: only the simulator accepts it
ISSUE_ID_BASE = 7_000_000_000  # GitHub's internal issue id, derived from the number

_WORD = re.compile(r"\w+")


def _words(text: str) -> list[str]:
    return [w.lower() for w in _WORD.findall(text)]


def _contains(words: list[str], phrase: list[str]) -> bool:
    n = len(phrase)
    return any(words[i : i + n] == phrase for i in range(len(words) - n + 1))


class GitHubSimulator:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._labels: list[str] = []
        self._issues: dict[int, dict[str, Any]] = {}
        self.requests: list[tuple[str, str]] = []  # (method, path), for debugging a task
        simulator = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def _handle(self) -> None:
                url = urlsplit(self.path)
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                try:
                    body = json.loads(raw) if raw else None
                except json.JSONDecodeError:
                    return self._send(400, {"message": "Problems parsing JSON"})
                status, answer = simulator.answer(
                    self.command, unquote(url.path), parse_qs(url.query), body
                )
                self._send(status, answer)

            def _send(self, status: int, body: Any) -> None:
                payload = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            do_GET = do_POST = do_PATCH = do_PUT = do_DELETE = _handle

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"
        self._thread = threading.Thread(
            target=self._server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        )

    def __enter__(self) -> GitHubSimulator:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._server.shutdown()
        self._server.server_close()

    # --- state --------------------------------------------------------------------------------

    def load(self, world: dict[str, Any] | None) -> None:
        """Replaces the state with a task's `world.github` (empty when None)."""
        world = world or {"labels": [], "issues": []}
        with self._lock:
            self._labels = list(world["labels"])
            self._issues = {}
            for issue in world["issues"]:
                self._issues[issue["number"]] = {
                    "number": issue["number"],
                    "title": issue["title"],
                    "body": issue.get("body", ""),
                    "state": issue.get("state", "open"),
                    "state_reason": issue.get("state_reason"),
                    "labels": list(issue.get("labels", [])),
                    "pull_request": issue.get("pull_request", False),
                    "duplicate_of": None,
                }
            self.requests = []

    def snapshot(self) -> dict[str, Any]:
        """The end state: {"labels": [...], "issues": {number: {...}}}."""
        with self._lock:
            return {"labels": list(self._labels), "issues": copy.deepcopy(self._issues)}

    # --- API ----------------------------------------------------------------------------------

    def answer(
        self, method: str, path: str, query: dict[str, list[str]], body: Any
    ) -> tuple[int, Any]:
        with self._lock:
            self.requests.append((method, path))
            if method == "POST" and re.fullmatch(r"/app/installations/[^/]+/access_tokens", path):
                return 201, {"token": INSTALLATION_TOKEN, "expires_at": "2099-01-01T00:00:00Z"}
            if method == "GET" and path == "/search/issues":
                return self._search(query.get("q", [""])[0], query)
            prefix = f"/repos/{REPO}"
            if not path.lower().startswith(prefix.lower()):
                return 404, {"message": "Not Found"}
            rest = path[len(prefix) :]
            if method == "GET" and rest == "/labels":
                return 200, [self._label(name) for name in self._labels]
            found = re.fullmatch(r"/issues/(\d+)(/labels)?", rest)
            if not found or int(found[1]) not in self._issues:
                return 404, {"message": "Not Found"}
            issue = self._issues[int(found[1])]
            if found[2]:
                return self._add_labels(issue, body) if method == "POST" else (404, _NOT_FOUND)
            if method == "GET":
                return 200, self._issue_json(issue)
            if method == "PATCH":
                return self._update(issue, body)
            return 404, _NOT_FOUND

    def _search(self, q: str, query: dict[str, list[str]]) -> tuple[int, Any]:
        try:
            tokens = shlex.split(q)
        except ValueError:
            return 422, {"message": "Validation Failed"}
        groups: list[list[str]] = [[]]
        filters: list[tuple[str, str]] = []
        for token in tokens:
            if token == "OR":
                groups.append([])
            elif re.fullmatch(r"-?[a-z_]+:.+", token):
                key, _, value = token.partition(":")
                filters.append((key, value))
            else:
                groups[-1].append(token)
        groups = [g for g in groups if g]
        repos = [v.lower() for k, v in filters if k == "repo"]
        if not repos:  # GitHub would search everywhere; the gateway always adds repo:
            return 422, {"message": "Validation Failed: no repo: qualifier"}
        if REPO.lower() not in repos:
            items: list[dict[str, Any]] = []
        else:
            items = [
                self._issue_json(issue)
                for issue in sorted(self._issues.values(), key=lambda i: -i["number"])
                if _filtered(issue, filters)
                and (not groups or any(_matches(issue, g) for g in groups))
            ]
        per_page = int(query.get("per_page", ["30"])[0])
        return 200, {
            "total_count": len(items),
            "incomplete_results": False,
            "items": items[:per_page],
        }

    def _add_labels(self, issue: dict[str, Any], body: Any) -> tuple[int, Any]:
        names = body.get("labels") if isinstance(body, dict) else body
        if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
            return 422, {"message": "Validation Failed"}
        for name in names:
            if name not in self._labels:
                self._labels.append(name)
            if name not in issue["labels"]:
                issue["labels"].append(name)
        return 200, [self._label(name) for name in issue["labels"]]

    def _update(self, issue: dict[str, Any], body: Any) -> tuple[int, Any]:
        if not isinstance(body, dict):
            return 422, {"message": "Validation Failed"}
        if "state" in body:
            if body["state"] not in ("open", "closed"):
                return 422, {"message": "Validation Failed"}
            issue["state"] = body["state"]
        if "state_reason" in body:
            issue["state_reason"] = body["state_reason"]
        if body.get("duplicate_issue_id") is not None:
            number = body["duplicate_issue_id"] - ISSUE_ID_BASE
            if number not in self._issues:
                return 422, {"message": "Validation Failed: duplicate issue not found"}
            issue["duplicate_of"] = number
        return 200, self._issue_json(issue)

    # --- shapes (as GitHub answers, trimmed) ----------------------------------------------------

    @staticmethod
    def _label(name: str) -> dict[str, Any]:
        return {
            "id": 10_000 + sum(name.encode()),
            "url": f"{API}/repos/{REPO}/labels/{name}",
            "name": name,
            "color": "ededed",
            "default": False,
            "description": None,
        }

    def _issue_json(self, issue: dict[str, Any]) -> dict[str, Any]:
        number = issue["number"]
        kind = "pull" if issue["pull_request"] else "issues"
        answer = {
            "url": f"{API}/repos/{REPO}/issues/{number}",
            "repository_url": f"{API}/repos/{REPO}",
            "html_url": f"{WEB}/{REPO}/{kind}/{number}",
            "id": ISSUE_ID_BASE + number,
            "number": number,
            "title": issue["title"],
            "body": issue["body"],
            "labels": [self._label(name) for name in issue["labels"]],
            "state": issue["state"],
            "state_reason": issue["state_reason"],
            "locked": False,
            "comments": 0,
            "closed_at": "2026-11-18T00:00:00Z" if issue["state"] == "closed" else None,
        }
        if issue["pull_request"]:
            answer["pull_request"] = {"url": f"{API}/repos/{REPO}/pulls/{number}"}
        return answer


_NOT_FOUND = {"message": "Not Found"}


def _matches(issue: dict[str, Any], terms: list[str]) -> bool:
    words = _words(f"{issue['title']} {issue['body']}")
    for term in terms:
        negate = term.startswith("-") and len(term) > 1
        phrase = _words(term[1:] if negate else term)
        if not phrase:
            continue
        if _contains(words, phrase) == negate:
            return False
    return True


def _filtered(issue: dict[str, Any], filters: list[tuple[str, str]]) -> bool:
    for key, value in filters:
        negate = key.startswith("-")
        key, value = key.lstrip("-"), value.lower()
        if key == "is" and value in ("issue", "pr", "pull-request"):
            ok = issue["pull_request"] == (value != "issue")
        elif (key == "is" and value in ("open", "closed")) or key == "state":
            ok = issue["state"] == value
        elif key == "label":
            ok = value.strip('"') in (name.lower() for name in issue["labels"])
        else:
            continue
        if ok == negate:
            return False
    return True
