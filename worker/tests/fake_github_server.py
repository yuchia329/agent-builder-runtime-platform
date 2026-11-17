"""A local HTTP server that answers like the GitHub REST API, for testing GitHubTools.

Register answers per route with `on(method, path, body)`; bodies are usually recorded responses
from tests/fixtures/github/ (cleaned: owner `octocat`, repo `Hello-World`). Unregistered routes
answer 404 like GitHub. `fail_next` queues error statuses for retry tests. Every request is kept.
"""

from __future__ import annotations

import json
import threading
from collections import deque
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

FIXTURES = Path(__file__).parent / "fixtures" / "github"
REPO = "octocat/Hello-World"


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / f"{name}.json").read_text())


@dataclass(frozen=True)
class Request:
    method: str
    path: str
    query: dict[str, list[str]]
    body: Any
    headers: dict[str, str]


class FakeGitHubServer:
    def __init__(self) -> None:
        self.requests: list[Request] = []
        self._routes: dict[tuple[str, str], tuple[int, Any, dict[str, str]]] = {}
        self._faults: deque[tuple[int, dict[str, str]]] = deque()
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                server._handle(self, "GET")

            def do_POST(self) -> None:  # noqa: N802
                server._handle(self, "POST")

            def log_message(self, *args: Any) -> None:
                pass

        self._http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._http.server_port}"
        self._thread = threading.Thread(target=self._http.serve_forever, daemon=True)

    def __enter__(self) -> FakeGitHubServer:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._http.shutdown()
        self._http.server_close()

    def on(
        self,
        method: str,
        path: str,
        body: Any,
        status: int = 200,
        headers: dict[str, str] | None = None,
    ) -> FakeGitHubServer:
        self._routes[(method, path)] = (status, body, headers or {})
        return self

    def fail_next(self, status: int, times: int = 1, headers: dict[str, str] | None = None) -> None:
        self._faults.extend([(status, headers or {})] * times)

    def _handle(self, h: BaseHTTPRequestHandler, method: str) -> None:
        url = urlsplit(h.path)
        raw = h.rfile.read(int(h.headers.get("Content-Length", 0)))
        self.requests.append(
            Request(
                method,
                url.path,
                parse_qs(url.query),
                json.loads(raw) if raw else None,
                dict(h.headers),
            )
        )
        if self._faults:
            status, headers = self._faults.popleft()
            return _send(h, status, {"message": "injected fault"}, headers)
        route = self._routes.get((method, url.path))
        if route is None:
            return _send(h, 404, {"message": "Not Found"})
        _send(h, *route)


def _send(
    h: BaseHTTPRequestHandler, status: int, body: Any, headers: dict[str, str] | None = None
) -> None:
    data = json.dumps(body).encode()
    h.send_response(status)
    h.send_header("Content-Type", "application/json")
    h.send_header("Content-Length", str(len(data)))
    for name, value in (headers or {}).items():
        h.send_header(name, value)
    h.end_headers()
    h.wfile.write(data)
