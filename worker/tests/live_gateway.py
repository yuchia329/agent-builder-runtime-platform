"""The real tool gateway (../tool-gateway) as a subprocess with the fake connector, plus signed
run tokens for it. Shared test infrastructure for the MCP tool adapter; nothing in the gateway's
folder is changed, it is only run (`uv run --project ../tool-gateway gateway`).

The subprocess gets a minimal environment (no `.env` values) and a throwaway P-256 key pair.
"""

import base64
import hashlib
import json
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

GATEWAY_PROJECT = Path(__file__).resolve().parents[2] / "tool-gateway"
FAKE_TOOLS = ["github.search_issues", "github.add_label", "github.close_issue"]


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class RunTokens:
    """Signs run tokens (contracts/run-token.schema.json) like the control plane."""

    def __init__(self) -> None:
        self.private = ec.generate_private_key(ec.SECP256R1())
        public = self.private.public_key()
        self.public_pem = public.public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        ).decode()
        numbers = public.public_numbers()
        jwk = {
            "crv": "P-256",
            "kty": "EC",
            "x": _b64(numbers.x.to_bytes(32, "big")),
            "y": _b64(numbers.y.to_bytes(32, "big")),
        }
        canonical = json.dumps(jwk, sort_keys=True, separators=(",", ":")).encode()
        self.kid = _b64(hashlib.sha256(canonical).digest())  # RFC 7638 thumbprint

    def sign(self, run_id: str = "run_gateway_test", **overrides: Any) -> str:
        now = int(time.time())
        claims = {
            "iss": "control-plane",
            "aud": "tool-gateway",
            "sub": run_id,
            "tenant": "tenant-test",
            "agent": "issue-triage",
            "agent_version": 1,
            "acts_as": "team-bot",
            "owner": "api:tenant-test",
            "started_by": "api:tenant-test",
            "tools": FAKE_TOOLS,
            "require_approval": [],
            "iat": now,
            "exp": now + 300,
            **overrides,
        }
        return jwt.encode(claims, self.private, algorithm="ES256", headers={"kid": self.kid})


class LiveGateway:
    def __init__(self, tokens: RunTokens) -> None:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self._tokens = tokens
        self._process: subprocess.Popen[bytes] | None = None

    def __enter__(self) -> "LiveGateway":
        uv = shutil.which("uv")
        if uv is None:
            raise RuntimeError("uv is needed to run the tool gateway")
        env = {
            k: os.environ[k] for k in ("PATH", "HOME", "TMPDIR", "UV_CACHE_DIR") if k in os.environ
        }
        env |= {
            "RUN_TOKEN_PUBLIC_KEY": self._tokens.public_pem,
            "CONNECTORS": "fake",
            "GATEWAY_PORT": str(self.port),
        }
        self._process = subprocess.Popen(
            [uv, "run", "--project", str(GATEWAY_PROJECT), "gateway"],
            cwd=GATEWAY_PROJECT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.time() + 60
        while time.time() < deadline:
            if self._process.poll() is not None:
                raise RuntimeError("tool gateway exited at startup")
            try:
                if httpx.get(f"{self.url}/health", timeout=1).status_code == 200:
                    return self
            except httpx.TransportError:
                time.sleep(0.1)
        self.__exit__()
        raise RuntimeError("tool gateway did not start")

    def __exit__(self, *exc: object) -> None:
        if self._process is not None and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(10)
            except subprocess.TimeoutExpired:
                self._process.kill()
