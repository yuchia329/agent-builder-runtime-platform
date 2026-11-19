"""Per-user login for acts_as owner (ADR 0003, ADR 0015): the login link, the OAuth flow, and the
stored token an owner run's calls use.

Flow, with no server memory between requests:
1. An owner run without a usable token fails with a tool error naming
   `<GATEWAY_PUBLIC_URL>/oauth/<provider>/start?ticket=...`; the ticket is a signed note
   (tenant, owner, provider; 10 minutes).
2. `begin`: a fresh nonce, kept in an HttpOnly cookie, and the OAuth `state`, a signed note with
   tenant, owner and nonce. The PKCE verifier is derived from the nonce with the secret, never
   stored.
3. `finish` (the callback): the state must be valid and its nonce equal the cookie's (the login
   finishes in the browser that started it, so nobody can link their account to another
   person's login); the code and derived verifier are exchanged for tokens, GET /user gives the
   login, and {access_token, refresh_token, expires_at, refresh_token_expires_at, login} is
   stored under (tenant, owner, provider).

Owner calls (`headers`) refresh the token within 5 minutes of expiry, or when the API answered
401, under a lock, saving the pair GitHub returns (it replaces the refresh token). A refused
refresh deletes the value and asks for a new login. Anyone holding a link while it is valid can
link their account to that owner: fine while an owner is the tenant's API key (ADR 0015).
"""

import logging
import secrets
import threading
import time
from collections.abc import Callable
from typing import Any

from gateway.oauth.github import GitHubOAuth, OAuthRejected, OAuthUnavailable
from gateway.oauth.signing import BadNote, Signer, challenge
from gateway.ports.token_store import CredentialKey, TokenStore
from gateway.tools.registry import ToolFailure

log = logging.getLogger("gateway.oauth")

LINK_SECONDS = 600
REFRESH_MARGIN_SECONDS = 300
AGAIN = "Run the agent again for a new one."


class LoginRefused(Exception):
    """The login cannot finish; the message is safe to show in the browser."""


class OAuthLogins:
    def __init__(
        self,
        store: TokenStore,
        provider: GitHubOAuth,
        signer: Signer,
        public_url: str,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._store = store
        self.provider = provider
        self._signer = signer
        self._public = public_url.rstrip("/")
        self._clock = clock
        self._lock = threading.Lock()

    @property
    def redirect_uri(self) -> str:
        return f"{self._public}/oauth/{self.provider.name}/callback"

    def login_url(self, tenant: str, owner: str) -> str:
        ticket = self._signer.sign(
            "ticket", {"t": tenant, "o": owner, "v": self.provider.name}, LINK_SECONDS
        )
        return f"{self._public}/oauth/{self.provider.name}/start?ticket={ticket}"

    def begin(self, ticket: str) -> tuple[str, str]:
        """The provider's authorize URL and the nonce for the browser's cookie."""
        note = self._note("ticket", ticket)
        nonce = secrets.token_urlsafe(32)
        state = self._signer.sign(
            "state", {"t": note["t"], "o": note["o"], "v": note["v"], "n": nonce}, LINK_SECONDS
        )
        verifier = self._signer.derive("pkce", nonce)
        return self.provider.authorize_url(self.redirect_uri, state, challenge(verifier)), nonce

    def finish(self, state: str, code: str, cookie_nonce: str | None) -> tuple[str, str]:
        """Stores the login; returns (owner, provider login)."""
        note = self._note("state", state)
        if not cookie_nonce or not secrets.compare_digest(cookie_nonce, str(note.get("n"))):
            raise LoginRefused(
                "This login did not start in this browser. Open the link from the run again."
            )
        verifier = self._signer.derive("pkce", note["n"])
        try:
            value = self.provider.exchange(code, self.redirect_uri, verifier)
            login = self.provider.login(value["access_token"])
        except OAuthRejected as e:
            raise LoginRefused(f"{e}. Open the link from the run again.") from None
        tenant, owner = note["t"], note["o"]
        self._store.put(self._key(tenant, owner), {**value, "login": login})
        log.info("Connected %s account %s for %s in %s", self.provider.name, login, owner, tenant)
        return owner, login

    def headers(self, tenant: str, owner: str, refresh: bool = False) -> dict[str, str]:
        """Authorization for an owner call; `refresh` after the API answered 401."""
        key = self._key(tenant, owner)
        with self._lock:
            value = self._store.get(key)
            if not value or not value.get("access_token"):
                raise ToolFailure(self._connect(tenant, owner, "has no connected account"))
            expires_at = value.get("expires_at")
            due = expires_at is not None and expires_at - REFRESH_MARGIN_SECONDS <= self._clock()
            if refresh or due:
                value = self._refreshed(key, value, tenant, owner)
            return {"Authorization": f"Bearer {value['access_token']}"}

    def _refreshed(
        self, key: CredentialKey, value: dict[str, Any], tenant: str, owner: str
    ) -> dict[str, Any]:
        refresh_token = value.get("refresh_token")
        refresh_expires = value.get("refresh_token_expires_at")
        if not refresh_token or (refresh_expires is not None and refresh_expires <= self._clock()):
            self._store.delete(key)
            raise ToolFailure(self._connect(tenant, owner, "must log in again"))
        try:
            fresh = self.provider.refresh(refresh_token)
        except OAuthRejected as e:
            log.info("Refresh refused for %s in %s: %s", owner, tenant, e)
            self._store.delete(key)
            raise ToolFailure(self._connect(tenant, owner, "must log in again")) from None
        except OAuthUnavailable as e:
            log.warning("Refresh failed for %s in %s: %s", owner, tenant, e)
            raise ToolFailure(
                f"{self.provider.name} login service is unavailable; try again later"
            ) from None
        value = {**value, **fresh, "refresh_token": fresh["refresh_token"] or refresh_token}
        self._store.put(key, value)
        return value

    def _connect(self, tenant: str, owner: str, problem: str) -> str:
        return (
            f"acts_as owner: {owner} {problem}. "
            f"Open {self.login_url(tenant, owner)} to connect a {self.provider.name} account "
            f"(the link works for {LINK_SECONDS // 60} minutes), then run the agent again."
        )

    def _note(self, purpose: str, text: str) -> dict[str, Any]:
        try:
            note = self._signer.verify(purpose, text)
        except BadNote as e:
            raise LoginRefused(f"This login link is {e}. {AGAIN}") from None
        if note.get("v") != self.provider.name or not note.get("t") or not note.get("o"):
            raise LoginRefused(f"This login link is not valid. {AGAIN}")
        return note

    def _key(self, tenant: str, owner: str) -> CredentialKey:
        return CredentialKey(tenant, owner, self.provider.name)
