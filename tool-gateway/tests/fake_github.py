"""A local HTTP server answering like the GitHub REST API, from recorded bodies.

Routes are registered with `on(method, path, body, status, headers)`; unregistered ones answer 404
like GitHub. `fail_next` queues error statuses (retries, rate limits). Every request is kept.

The GitHub App token exchange (POST /app/installations/{INSTALLATION_ID}/access_tokens) is answered
apart: it is kept in `token_requests`, not `requests`, and answers `app_token` (or the statuses
queued with `fail_token_next`). `app_store()` holds the matching App credential. Deterministic,
shared test infrastructure.
"""

import json
import threading
from collections import deque
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from gateway.adapters.token_store_memory import MemoryTokenStore
from gateway.ports.token_store import CredentialKey

FIXTURES = Path(__file__).parent / "fixtures" / "github"
REPO = "octocat/Hello-World"
APP_ID = "1001"
INSTALLATION_ID = "2002"
APP_TOKEN = "ghs_test_secret_value"
TOKEN_PATH = f"/app/installations/{INSTALLATION_ID}/access_tokens"
APP_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
APP_PEM = APP_KEY.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.TraditionalOpenSSL,
    serialization.NoEncryption(),
).decode()


def app_credential() -> dict[str, str]:
    return {"app_id": APP_ID, "private_key": APP_PEM, "installation_id": INSTALLATION_ID}


def app_store(tenant: str = "tenant-test") -> MemoryTokenStore:
    store = MemoryTokenStore()
    store.put(CredentialKey(tenant, "team-bot", "github"), app_credential())
    return store


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
        self.token_requests: list[Request] = []
        self.token_failures: deque[int] = deque()
        self.app_token = APP_TOKEN
        self.app_token_expires_at = "2099-01-01T00:00:00Z"
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def _handle(self) -> None:
                url = urlsplit(self.path)
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                request = Request(
                    self.command,
                    url.path,
                    parse_qs(url.query),
                    {k: v for k, v in self.headers.items()},
                    json.loads(raw) if raw else None,
                )
                if (self.command, url.path) == ("POST", TOKEN_PATH):
                    fake.token_requests.append(request)
                    if fake.token_failures:
                        return self._send(fake.token_failures.popleft(), {"message": "no"}, {})
                    token = {"token": fake.app_token, "expires_at": fake.app_token_expires_at}
                    return self._send(201, token, {})
                fake.requests.append(request)
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

    def fail_token_next(self, status: int, times: int = 1) -> None:
        self.token_failures.extend([status] * times)

    def __enter__(self) -> "FakeGitHub":
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._server.shutdown()
        self._server.server_close()
