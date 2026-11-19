"""Run token checks (ADR 0003, contracts/run-token.schema.json).

The control plane signs an ES256 JWT per GET /internal/runs/{id}; its header `kid` is the RFC 7638
thumbprint of the signing key's public half. The gateway holds only the public key and accepts a
token when the signature, `kid`, issuer, audience and expiry are right and the claims follow the
contract. Reasons for a refusal are for logs only; the caller learns nothing beyond "unauthorized".
Tokens are never logged.
"""

import base64
import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import load_der_public_key

from gateway.contracts import Contracts

ISSUER = "control-plane"
AUDIENCE = "tool-gateway"
LEEWAY_SECONDS = 30  # clock difference between control plane and gateway


class TokenRejected(Exception):
    """The token is not acceptable. The message is a reason for logs, never sent to the caller."""


@dataclass(frozen=True)
class RunClaims:
    run_id: str
    tenant: str
    agent: str
    agent_version: int
    acts_as: str
    owner: str
    started_by: str
    tools: frozenset[str]
    require_approval: frozenset[str]
    expires_at: int

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "RunClaims":
        return cls(
            run_id=payload["sub"],
            tenant=payload["tenant"],
            agent=payload["agent"],
            agent_version=payload["agent_version"],
            acts_as=payload["acts_as"],
            owner=payload["owner"],
            started_by=payload["started_by"],
            tools=frozenset(payload["tools"]),
            require_approval=frozenset(payload["require_approval"]),
            expires_at=payload["exp"],
        )


def load_public_key(text: str) -> ec.EllipticCurvePublicKey:
    """PEM (any header) or its base64 body on one line; literal \\n counts as a line break."""
    body = "".join(
        line
        for line in text.replace("\\n", "\n").splitlines()
        if line.strip() and not line.startswith("-----")
    )
    try:
        key = load_der_public_key(base64.b64decode(body.replace(" ", "")))
    except ValueError as e:
        raise ValueError("RUN_TOKEN_PUBLIC_KEY is not an X.509 public key") from e
    if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(key.curve, ec.SECP256R1):
        raise ValueError("RUN_TOKEN_PUBLIC_KEY must be a P-256 key (ES256)")
    return key


def thumbprint(key: ec.EllipticCurvePublicKey) -> str:
    """RFC 7638 JWK thumbprint (SHA-256, base64url), as the control plane computes `kid`."""
    numbers = key.public_numbers()

    def b64(value: int) -> str:
        return base64.urlsafe_b64encode(value.to_bytes(32, "big")).rstrip(b"=").decode()

    jwk = json.dumps(
        {"crv": "P-256", "kty": "EC", "x": b64(numbers.x), "y": b64(numbers.y)},
        separators=(",", ":"),
        sort_keys=True,
    )
    return base64.urlsafe_b64encode(hashlib.sha256(jwk.encode()).digest()).rstrip(b"=").decode()


class RunTokenVerifier:
    def __init__(
        self,
        public_key: str,
        contracts: Contracts,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._key = load_public_key(public_key)
        self.key_id = thumbprint(self._key)
        self._claims_schema = contracts.validator("run-token")
        self._now = now

    def verify(self, token: str) -> RunClaims:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as e:
            raise TokenRejected("not a JWT") from e
        if header.get("alg") != "ES256":
            raise TokenRejected(f"algorithm {header.get('alg')!r}")
        if header.get("kid") != self.key_id:
            raise TokenRejected("unknown key id")
        # Expiry is checked here against the injectable clock, not PyJWT's own.
        options = {
            "require": ["exp", "iat", "sub", "aud", "iss"],
            "verify_exp": False,
            "verify_iat": False,
        }
        try:
            payload = jwt.decode(
                token,
                self._key,
                algorithms=["ES256"],
                audience=AUDIENCE,
                issuer=ISSUER,
                options=options,
            )
        except jwt.PyJWTError as e:
            raise TokenRejected(type(e).__name__) from e
        errors = [e.message for e in self._claims_schema.iter_errors(payload)]
        if errors:
            raise TokenRejected("claims do not follow the contract: " + "; ".join(errors[:3]))
        if payload["exp"] + LEEWAY_SECONDS < self._now():
            raise TokenRejected("expired")
        if payload["iat"] - LEEWAY_SECONDS > self._now():
            raise TokenRejected("issued in the future")
        if payload["exp"] <= payload["iat"]:
            raise TokenRejected("exp not after iat")
        if not set(payload["require_approval"]) <= set(payload["tools"]):
            raise TokenRejected("require_approval not a subset of tools")
        return RunClaims.from_payload(payload)
