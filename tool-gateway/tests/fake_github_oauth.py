"""A local server answering like GitHub's user login for a GitHub App (ADR 0015).

- GET /login/oauth/authorize: the person approves at once (302 to redirect_uri with code and
  state), or denies when `deny` is set (error=access_denied). The code remembers client id,
  redirect URI and PKCE challenge.
- POST /login/oauth/access_token (form): checks client id and secret; a code must match its
  redirect URI and S256(code_verifier) must equal the challenge; a refresh token is used once and
  replaced. Refusals are HTTP 200 with {"error": ...} like GitHub's. `fail_token_next` queues
  statuses (5xx).
- GET /user: {"login": LOGIN} for a current access token, else 401.

Every request is kept in `requests` (path, query, form). Deterministic, shared test infrastructure.
"""

import base64
import hashlib
import json
import threading
from collections import deque
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit

CLIENT_ID = "Iv1.test-client"
CLIENT_SECRET = "client-secret-test-value"
STATE_SECRET = "state-secret-test-value-0123456789abcdef"
LOGIN = "test-person"
EXPIRES_IN = 28800
REFRESH_EXPIRES_IN = 15897600


@dataclass(frozen=True)
class Request:
    method: str
    path: str
    query: dict[str, str]
    form: dict[str, str]
    headers: dict[str, str]


class FakeGitHubOAuth:
    def __init__(self) -> None:
        self.requests: list[Request] = []
        self.codes: dict[str, tuple[str, str, str]] = {}  # code -> client, redirect, challenge
        self.access_tokens: set[str] = set()
        self.refresh_tokens: set[str] = set()
        self.token_failures: deque[int] = deque()
        self.deny = False
        self.expires = True  # the App's "Expire user authorization tokens"
        self._issued = 0
        self._lock = threading.Lock()
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def _handle(self) -> None:
                url = urlsplit(self.path)
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length).decode() if length else ""
                request = Request(
                    self.command,
                    url.path,
                    dict(parse_qsl(url.query)),
                    dict(parse_qsl(raw)),
                    {k.lower(): v for k, v in self.headers.items()},
                )
                fake.requests.append(request)
                route = (self.command, url.path)
                if route == ("GET", "/login/oauth/authorize"):
                    return self._redirect(fake.authorize(request.query))
                if route == ("POST", "/login/oauth/access_token"):
                    if fake.token_failures:
                        return self._send(fake.token_failures.popleft(), {"message": "failure"})
                    return self._send(200, fake.token(request.form))
                if route == ("GET", "/user"):
                    bearer = request.headers.get("authorization", "").removeprefix("Bearer ")
                    if bearer in fake.access_tokens:
                        return self._send(200, {"login": LOGIN, "id": 1})
                    return self._send(401, {"message": "Bad credentials"})
                self._send(404, {"message": "Not Found"})

            def _redirect(self, location: str) -> None:
                self.send_response(302)
                self.send_header("Location", location)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def _send(self, status: int, body: Any) -> None:
                payload = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            do_GET = do_POST = _handle

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"
        self._thread = threading.Thread(
            target=self._server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        )

    def authorize(self, query: dict[str, str]) -> str:
        redirect = query["redirect_uri"]
        if self.deny:
            return f"{redirect}?{urlencode({'error': 'access_denied', 'state': query['state']})}"
        assert query.get("code_challenge_method") == "S256"
        with self._lock:
            self._issued += 1
            code = f"code-{self._issued}"
        self.codes[code] = (query["client_id"], redirect, query["code_challenge"])
        return f"{redirect}?{urlencode({'code': code, 'state': query['state']})}"

    def token(self, form: dict[str, str]) -> dict[str, Any]:
        if (form.get("client_id"), form.get("client_secret")) != (CLIENT_ID, CLIENT_SECRET):
            return {"error": "incorrect_client_credentials"}
        if form.get("grant_type") == "refresh_token":
            if form.get("refresh_token") not in self.refresh_tokens:
                return {"error": "bad_refresh_token"}
            self.refresh_tokens.discard(form["refresh_token"])
            return self.issue()
        client, redirect, expected = self.codes.pop(form.get("code", ""), (None, None, None))
        if client is None or client != form["client_id"]:
            return {"error": "bad_verification_code"}
        if form.get("redirect_uri") != redirect:
            return {"error": "redirect_uri_mismatch"}
        verifier = form.get("code_verifier", "").encode()
        digest = base64.urlsafe_b64encode(hashlib.sha256(verifier).digest()).rstrip(b"=").decode()
        if digest != expected:
            return {"error": "bad_verification_code"}
        return self.issue()

    def issue(self) -> dict[str, Any]:
        with self._lock:
            self._issued += 1
            n = self._issued
        access = f"ghu_test_access_{n}"
        self.access_tokens.add(access)
        body = {"access_token": access, "token_type": "bearer", "scope": ""}
        if self.expires:
            refresh = f"ghr_test_refresh_{n}"
            self.refresh_tokens.add(refresh)
            body |= {
                "expires_in": EXPIRES_IN,
                "refresh_token": refresh,
                "refresh_token_expires_in": REFRESH_EXPIRES_IN,
            }
        return body

    def revoke_all(self) -> None:
        self.access_tokens.clear()
        self.refresh_tokens.clear()

    def fail_token_next(self, status: int, times: int = 1) -> None:
        self.token_failures.extend([status] * times)

    def token_forms(self) -> list[dict[str, str]]:
        return [r.form for r in self.requests if r.path == "/login/oauth/access_token"]

    def __enter__(self) -> "FakeGitHubOAuth":
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._server.shutdown()
        self._server.server_close()
