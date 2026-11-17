"""A local HTTP server that speaks the internal run API, for testing HttpControlPlane.

State and status rules come from InMemoryControlPlane. Every PATCH body is checked against the
contract's RunUpdate schema (400 if invalid) and every request must carry a SigV4 signature (403
otherwise). `fail_next` injects error responses to test retries.
"""

from __future__ import annotations

import json
import threading
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from botocore.credentials import Credentials
from contracts import contract_errors

from worker.adapters.control_plane_http import HttpControlPlane, Signer, sigv4_signer
from worker.adapters.control_plane_memory import InMemoryControlPlane
from worker.core.errors import RunConflict, RunNotFound

PREFIX = "/internal/runs/"


def fake_signer() -> Signer:
    """SigV4 signer with fixed fake credentials (no AWS credential lookup)."""
    return sigv4_signer(lambda: Credentials("test-key", "test-secret"), "us-east-1")


def http_client(server: FakeControlPlaneServer, **kwargs: Any) -> HttpControlPlane:
    """HttpControlPlane pointed at the fake server, with no real sleeping between retries."""
    return HttpControlPlane(server.url, fake_signer(), sleep=lambda _: None, **kwargs)


class FakeControlPlaneServer:
    def __init__(self) -> None:
        self.state = InMemoryControlPlane()
        self.requests: list[tuple[str, str, dict[str, str]]] = []  # (method, path, headers)
        self._faults: deque[int] = deque()
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                server._handle(self, "GET")

            def do_PATCH(self) -> None:  # noqa: N802
                server._handle(self, "PATCH")

            def log_message(self, *args: Any) -> None:
                pass

        self._http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._http.server_port}"
        self._thread = threading.Thread(target=self._http.serve_forever, daemon=True)

    def __enter__(self) -> FakeControlPlaneServer:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._http.shutdown()
        self._http.server_close()

    def fail_next(self, status: int, times: int = 1) -> None:
        self._faults.extend([status] * times)

    def _handle(self, h: BaseHTTPRequestHandler, method: str) -> None:
        self.requests.append((method, h.path, dict(h.headers)))
        if self._faults:
            return _send(h, self._faults.popleft(), _problem("injected fault"))
        if not h.headers.get("Authorization", "").startswith("AWS4-HMAC-SHA256 "):
            return _send(h, 403, _problem("missing SigV4 signature"))
        if not h.path.startswith(PREFIX):
            return _send(h, 404, _problem("no such path"))
        run_id = h.path[len(PREFIX) :]

        if method == "GET":
            try:
                return _send(h, 200, self.state.run_json(run_id))
            except KeyError:
                return _send(h, 404, _problem(f"run {run_id} not found"))

        body = json.loads(h.rfile.read(int(h.headers.get("Content-Length", 0))) or b"null")
        if errors := contract_errors("RunUpdate", body):
            return _send(h, 400, _problem("; ".join(errors)))
        try:
            self.state.apply(run_id, body)
        except RunNotFound:
            return _send(h, 404, _problem(f"run {run_id} not found"))
        except RunConflict as e:
            return _send(h, 409, _problem(str(e)))
        return _send(h, 200, {"run_id": run_id, **body})


def _problem(detail: str) -> dict[str, Any]:
    return {"type": "about:blank", "detail": detail}


def _send(h: BaseHTTPRequestHandler, status: int, body: Any) -> None:
    data = json.dumps(body).encode()
    h.send_response(status)
    content_type = "application/json" if status < 400 else "application/problem+json"
    h.send_header("Content-Type", content_type)
    h.send_header("Content-Length", str(len(data)))
    h.end_headers()
    h.wfile.write(data)
