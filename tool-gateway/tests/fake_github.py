"""A local HTTP server answering like the GitHub REST API, from recorded bodies.

Routes are registered with `on(method, path, body, status, headers)`; unregistered ones answer 404
like GitHub. `fail_next` queues error statuses (retries, rate limits). Every request is kept.
Deterministic, shared test infrastructure.
"""

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
    headers: dict[str, str]
    body: Any


class FakeGitHub:
    def __init__(self) -> None:
        self.routes: dict[tuple[str, str], tuple[int, Any, dict[str, str]]] = {}
        self.failures: deque[tuple[int, dict[str, str]]] = deque()
        self.requests: list[Request] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def _handle(self) -> None:
                url = urlsplit(self.path)
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                fake.requests.append(
                    Request(
                        self.command,
                        url.path,
                        parse_qs(url.query),
                        {k: v for k, v in self.headers.items()},
                        json.loads(raw) if raw else None,
                    )
                )
                if fake.failures:
                    status, headers = fake.failures.popleft()
                    return self._send(status, {"message": "failure"}, headers)
                status, body, headers = fake.routes.get(
                    (self.command, url.path), (404, {"message": "Not Found"}, {})
                )
                self._send(status, body, headers)

            def _send(self, status: int, body: Any, headers: dict[str, str]) -> None:
                payload = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                for k, v in headers.items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(payload)

            do_GET = do_POST = do_PATCH = do_PUT = do_DELETE = _handle

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"
        self._thread = threading.Thread(
            target=self._server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        )

    def on(self, method: str, path: str, body: Any, status: int = 200, headers=None) -> None:
        self.routes[(method, path)] = (status, body, headers or {})

    def fail_next(self, status: int, times: int = 1, headers: dict[str, str] | None = None) -> None:
        self.failures.extend([(status, headers or {})] * times)

    def __enter__(self) -> "FakeGitHub":
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._server.shutdown()
        self._server.server_close()
