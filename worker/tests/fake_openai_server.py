"""A local HTTP server that speaks the OpenAI Chat Completions format, for testing
OpenAICompatModel without a network.

Replies are played back in order from `reply(...)` (usually recorded fixtures from
tests/fixtures/<provider>/). Every request body and header set is kept for assertions.
"""

from __future__ import annotations

import json
import threading
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).parent / "fixtures"
PATH = "/v1/chat/completions"


def fixture(provider: str, name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / provider / f"{name}.json").read_text())


class FakeOpenAIServer:
    def __init__(self) -> None:
        self.requests: list[tuple[dict[str, Any], dict[str, str]]] = []  # (body, headers)
        self._replies: deque[tuple[int, Any, dict[str, str]]] = deque()
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                server._handle(self)

            def log_message(self, *args: Any) -> None:
                pass

        self._http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base_url = f"http://127.0.0.1:{self._http.server_port}/v1"
        self._thread = threading.Thread(target=self._http.serve_forever, daemon=True)

    def __enter__(self) -> FakeOpenAIServer:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._http.shutdown()
        self._http.server_close()

    def reply(
        self, *bodies: Any, status: int = 200, headers: dict[str, str] | None = None
    ) -> FakeOpenAIServer:
        self._replies.extend((status, b, headers or {}) for b in bodies)
        return self

    @property
    def bodies(self) -> list[dict[str, Any]]:
        return [body for body, _ in self.requests]

    def _handle(self, h: BaseHTTPRequestHandler) -> None:
        body = json.loads(h.rfile.read(int(h.headers.get("Content-Length", 0))) or b"null")
        self.requests.append((body, dict(h.headers)))
        if h.path != PATH:
            return _send(h, 404, {"error": {"message": f"no such path {h.path}"}})
        if not self._replies:
            return _send(h, 500, {"error": {"message": "fake server has no reply queued"}})
        status, reply, headers = self._replies.popleft()
        _send(h, status, reply, headers)


def _send(
    h: BaseHTTPRequestHandler, status: int, body: Any, headers: dict[str, str] | None = None
) -> None:
    data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
    h.send_response(status)
    h.send_header("Content-Type", "application/json")
    h.send_header("Content-Length", str(len(data)))
    for name, value in (headers or {}).items():
        h.send_header(name, value)
    h.end_headers()
    h.wfile.write(data)
