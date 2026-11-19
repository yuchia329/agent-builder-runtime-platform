"""GitHub App credential for acts_as team-bot (ADR 0003): an app JWT exchanged for an installation
token.

Stored value (TokenStore, under (tenant, "team-bot", "github")):
{"app_id", "private_key" (PEM), "installation_id"}.

The app JWT (RS256, `iat` 60 s in the past for clock drift, valid 9 minutes, GitHub allows 10) is
sent only to POST /app/installations/{id}/access_tokens. The installation token it returns lasts
an hour; it is cached in memory per API, app and installation until 5 minutes before it expires.
Errors carry a status, never the key, the JWT or a token.
"""

import threading
import time
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx2
import jwt

from gateway.tools.registry import ToolFailure

FIELDS = ("app_id", "private_key", "installation_id")
JWT_BACKDATE_SECONDS = 60
JWT_LIFETIME_SECONDS = 540
REFRESH_MARGIN_SECONDS = 300
ATTEMPTS = 3
TIMEOUT_SECONDS = 10.0
HEADERS = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}


@dataclass(frozen=True)
class AppCredential:
    app_id: str
    private_key: str
    installation_id: str

    @classmethod
    def from_value(cls, value: dict[str, Any]) -> "AppCredential":
        missing = [f for f in FIELDS if not str(value.get(f) or "").strip()]
        if missing:
            raise ToolFailure(
                f"The GitHub App credential is missing {', '.join(missing)}; "
                "ask an operator to fix it"
            )
        return cls(*(str(value[f]).strip() for f in FIELDS))

    def __repr__(self) -> str:  # never show the key
        return f"AppCredential(app_id={self.app_id}, installation_id={self.installation_id})"


class GitHubAppTokens:
    """Installation tokens, cached. Thread-safe; one instance per process."""

    def __init__(
        self,
        http: httpx2.Client | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._http = http or httpx2.Client(timeout=TIMEOUT_SECONDS)
        self._clock = clock
        self._sleep = sleep
        self._cache: dict[tuple[str, str, str], tuple[str, float]] = {}
        self._lock = threading.Lock()

    def token(self, credential: AppCredential, base_url: str, renew: bool = False) -> str:
        """A valid installation token; `renew` skips the cache (after GitHub answered 401)."""
        key = (base_url, credential.app_id, credential.installation_id)
        with self._lock:
            cached = self._cache.get(key)
            if cached and not renew and cached[1] - REFRESH_MARGIN_SECONDS > self._clock():
                return cached[0]
            token, expires_at = self._exchange(credential, base_url)
            self._cache[key] = (token, expires_at)
            return token

    def _exchange(self, credential: AppCredential, base_url: str) -> tuple[str, float]:
        now = int(self._clock())
        claims = {
            "iat": now - JWT_BACKDATE_SECONDS,
            "exp": now + JWT_LIFETIME_SECONDS,
            "iss": credential.app_id,
        }
        try:
            app_jwt = jwt.encode(claims, credential.private_key, algorithm="RS256")
        except Exception:  # a malformed or wrong-type key; the error text may quote it
            raise ToolFailure(
                "The GitHub App private key cannot be used; ask an operator to fix it"
            ) from None
        installation = urllib.parse.quote(credential.installation_id, safe="")
        url = f"{base_url.rstrip('/')}/app/installations/{installation}/access_tokens"
        headers = {**HEADERS, "Authorization": f"Bearer {app_jwt}"}
        for attempt in range(1, ATTEMPTS + 1):
            try:
                response = self._http.post(url, headers=headers)
            except httpx2.TransportError:
                if attempt == ATTEMPTS:
                    raise ToolFailure("github is unreachable; try again later") from None
                self._sleep(attempt)
                continue
            if response.status_code >= 500 and attempt < ATTEMPTS:
                self._sleep(attempt)
                continue
            break
        status = response.status_code
        if status in (401, 403, 404):
            raise ToolFailure(
                f"GitHub rejected the App credential for team-bot ({status}); "
                "ask an operator to check the app id, key and installation"
            )
        if status >= 400:
            raise ToolFailure(f"GitHub App token request failed ({status}); try again later")
        try:
            body = response.json()
            token = body["token"]
            expires_at = datetime.fromisoformat(body["expires_at"]).timestamp()
        except (ValueError, KeyError, TypeError):
            raise ToolFailure("GitHub App token response was not understood") from None
        if not isinstance(token, str) or not token:
            raise ToolFailure("GitHub App token response was not understood")
        return token, expires_at
