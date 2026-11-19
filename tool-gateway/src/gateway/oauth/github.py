"""GitHub user login through the GitHub App (ADR 0015): user-to-server tokens.

The App's client id and secret authorize a person at github.com/login/oauth/authorize (state and
PKCE S256) and exchange the code at /login/oauth/access_token. The token can do only what both the
App (its permissions and installed repositories) and the person may do. With "Expire user
authorization tokens" on, it lasts 8 hours and comes with a refresh token (6 months) that GitHub
replaces on every refresh.

GitHub answers a refused exchange or refresh with HTTP 200 and an `error` field. Errors carry
GitHub's error code or a status, never a code, token or secret.
"""

import time
import urllib.parse
from collections.abc import Callable
from typing import Any

import httpx2

WEB_URL = "https://github.com"
API_URL = "https://api.github.com"
TIMEOUT_SECONDS = 10.0
API_HEADERS = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}


class OAuthRejected(Exception):
    """GitHub refused (a bad code, verifier or refresh token; a revoked login). Do not retry."""


class OAuthUnavailable(Exception):
    """GitHub could not be reached or failed (network, 5xx). Worth trying later."""


class GitHubOAuth:
    name = "github"

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        *,
        web_url: str = WEB_URL,
        api_url: str = API_URL,
        http: httpx2.Client | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.client_id = client_id
        self._client_secret = client_secret
        self._web = web_url.rstrip("/")
        self._api = api_url.rstrip("/")
        self._http = http or httpx2.Client(timeout=TIMEOUT_SECONDS)
        self._clock = clock

    def __repr__(self) -> str:  # never show the secret
        return f"GitHubOAuth(client_id={self.client_id})"

    def authorize_url(self, redirect_uri: str, state: str, code_challenge: str) -> str:
        query = urllib.parse.urlencode(
            {
                "client_id": self.client_id,
                "redirect_uri": redirect_uri,
                "state": state,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
            }
        )
        return f"{self._web}/login/oauth/authorize?{query}"

    def exchange(self, code: str, redirect_uri: str, code_verifier: str) -> dict[str, Any]:
        """The stored value for a new login: tokens and their expiry times (epoch seconds)."""
        return self._token(
            {"code": code, "redirect_uri": redirect_uri, "code_verifier": code_verifier}
        )

    def refresh(self, refresh_token: str) -> dict[str, Any]:
        return self._token({"grant_type": "refresh_token", "refresh_token": refresh_token})

    def login(self, access_token: str) -> str:
        """The GitHub login the token acts as (GET /user)."""
        headers = {**API_HEADERS, "Authorization": f"Bearer {access_token}"}
        try:
            response = self._http.get(f"{self._api}/user", headers=headers)
        except httpx2.TransportError:
            raise OAuthUnavailable("GitHub is unreachable") from None
        if response.status_code in (401, 403):
            raise OAuthRejected(f"GitHub refused the new token ({response.status_code})")
        if response.status_code != 200:
            raise OAuthUnavailable(f"GitHub answered {response.status_code}")
        try:
            login = response.json()["login"]
        except (ValueError, KeyError, TypeError):
            raise OAuthUnavailable("GitHub's user answer was not understood") from None
        if not isinstance(login, str) or not login:
            raise OAuthUnavailable("GitHub's user answer was not understood")
        return login

    def _token(self, form: dict[str, str]) -> dict[str, Any]:
        form = {**form, "client_id": self.client_id, "client_secret": self._client_secret}
        try:
            response = self._http.post(
                f"{self._web}/login/oauth/access_token",
                data=form,
                headers={"Accept": "application/json"},
            )
        except httpx2.TransportError:
            raise OAuthUnavailable("GitHub is unreachable") from None
        if response.status_code >= 500:
            raise OAuthUnavailable(f"GitHub answered {response.status_code}")
        try:
            body = response.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            status = response.status_code
            raise OAuthUnavailable(f"GitHub's token answer was not understood ({status})")
        if body.get("error") or response.status_code >= 400:
            error = str(body.get("error") or response.status_code)
            raise OAuthRejected(f"GitHub refused ({_safe(error)})")
        token = body.get("access_token")
        if not isinstance(token, str) or not token:
            raise OAuthUnavailable("GitHub's token answer was not understood")
        now = self._clock()
        return {
            "access_token": token,
            "refresh_token": body.get("refresh_token") or None,
            "expires_at": _at(now, body.get("expires_in")),
            "refresh_token_expires_at": _at(now, body.get("refresh_token_expires_in")),
        }


def _at(now: float, seconds: Any) -> int | None:
    """Expiry time; None when GitHub sends none (the App's token expiry is turned off)."""
    try:
        return int(now) + int(seconds) if seconds not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _safe(code: str) -> str:
    """GitHub's error code (`bad_verification_code`), kept short and plain."""
    return "".join(c for c in code if c.isalnum() or c == "_")[:64] or "unknown"
