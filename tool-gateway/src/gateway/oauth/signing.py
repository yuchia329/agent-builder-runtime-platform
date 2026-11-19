"""Short-lived signed notes (HMAC-SHA256 with OAUTH_STATE_SECRET), so the login flow keeps no
server memory between requests (Lambda, ADR 0007).

A note is `<base64url JSON>.<base64url MAC>`; the JSON carries its purpose (`p`) and expiry
(`exp`), so a login ticket is never accepted as an OAuth state or the other way round. `derive`
gives values recomputable from the secret (the PKCE verifier from the state's nonce). Labels keep
the two uses apart: the MAC covers `sig.<data>`, a derived value `<label>.<value>`.
"""

import base64
import hashlib
import hmac
import json
import time
from collections.abc import Callable
from typing import Any


class BadNote(Exception):
    """A note that is forged, malformed, for another purpose, or expired. The message is safe to
    show: it never contains the note or the secret."""


class Signer:
    def __init__(self, secret: str, clock: Callable[[], float] = time.time) -> None:
        self._key = secret.encode()
        self._clock = clock

    def sign(self, purpose: str, payload: dict[str, Any], lifetime_seconds: int) -> str:
        body = {**payload, "p": purpose, "exp": int(self._clock()) + lifetime_seconds}
        data = _b64(json.dumps(body, sort_keys=True, separators=(",", ":")).encode())
        return f"{data}.{_b64(self._mac('sig', data))}"

    def verify(self, purpose: str, note: str) -> dict[str, Any]:
        data, _, mac = (note or "").partition(".")
        if not data or not mac or not hmac.compare_digest(mac, _b64(self._mac("sig", data))):
            raise BadNote("not valid")
        try:
            body = json.loads(_unb64(data))
        except ValueError:
            raise BadNote("not valid") from None
        if not isinstance(body, dict) or body.get("p") != purpose:
            raise BadNote("not valid")
        if not isinstance(body.get("exp"), int) or body["exp"] <= self._clock():
            raise BadNote("expired")
        return body

    def derive(self, label: str, value: str) -> str:
        """43 URL-safe characters: valid as a PKCE code_verifier (RFC 7636: 43 to 128)."""
        return _b64(self._mac(label, value))

    def _mac(self, label: str, value: str) -> bytes:
        return hmac.new(self._key, f"{label}.{value}".encode(), hashlib.sha256).digest()


def challenge(verifier: str) -> str:
    """PKCE S256 code_challenge for a verifier."""
    return _b64(hashlib.sha256(verifier.encode()).digest())


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
