"""A local HTTP server answering GET /internal/runs/{id} like the control plane, from runs put with
`put`. `fail_next` queues error statuses; `garbage` answers a non-JSON body. Every request is kept.
Deterministic, shared test infrastructure."""

import json
import threading
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import unquote


class FakeControlPlane:
    def __init__(self) -> None:
        self.runs: dict[str, dict[str, Any]] = {}
        self.failures: deque[int] = deque()
        self.garbage = False
        self.requests: list[tuple[str, dict[str, str]]] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def do_GET(self) -> None:
                fake.requests.append((self.path, dict(self.headers.items())))
                if fake.failures:
                    return self._send(fake.failures.popleft(), {"message": "failure"})
                if fake.garbage:
                    return self._send(200, None, raw=b"<html>")
                prefix = "/internal/runs/"
                run = fake.runs.get(unquote(self.path[len(prefix) :]))
                if not self.path.startswith(prefix) or run is None:
                    return self._send(404, {"message": "Not found"})
                self._send(200, run)

            def _send(self, status: int, body: Any, raw: bytes | None = None) -> None:
                payload = raw if raw is not None else json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"
        self._thread = threading.Thread(
            target=self._server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        )

    def put(self, run: dict[str, Any]) -> None:
        self.runs[run["run_id"]] = run

    def fail_next(self, status: int, times: int = 1) -> None:
        self.failures.extend([status] * times)

    def __enter__(self) -> "FakeControlPlane":
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._server.shutdown()
        self._server.server_close()
