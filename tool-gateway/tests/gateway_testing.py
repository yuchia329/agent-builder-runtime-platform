"""Shared test helpers: throwaway keys, signed run tokens, a live gateway, an MCP client."""

import contextlib
import socket
import threading
import time
from typing import Any

import httpx2
import jwt
import uvicorn
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from gateway.app import create_app
from gateway.auth.run_token import RunTokenVerifier, thumbprint
from gateway.config import REPO_CONTRACTS
from gateway.connectors.fake import FakeConnector
from gateway.contracts import Contracts
from gateway.tools.registry import Registry

CONTRACTS = Contracts(REPO_CONTRACTS)
TOOLS = ["github.search_issues", "github.add_label", "github.close_issue", "slack.post_message"]


class Keys:
    def __init__(self, curve: ec.EllipticCurve | None = None) -> None:
        self.private = ec.generate_private_key(curve or ec.SECP256R1())
        self.public_pem = (
            self.private.public_key()
            .public_bytes(
                serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
            )
            .decode()
        )
        self.kid = thumbprint(self.private.public_key()) if curve is None else "other"


def claims(**overrides: Any) -> dict[str, Any]:
    now = int(time.time())
    base = {
        "iss": "control-plane",
        "aud": "tool-gateway",
        "sub": "run_test",
        "tenant": "tenant-test",
        "agent": "issue-triage",
        "agent_version": 4,
        "acts_as": "team-bot",
        "owner": "api:tenant-test",
        "started_by": "webhook:github",
        "tools": ["github.search_issues", "github.add_label"],
        "require_approval": [],
        "iat": now,
        "exp": now + 180,
    }
    return {**base, **overrides}


def token(keys: Keys, payload: dict[str, Any] | None = None, kid: str | None = None) -> str:
    return jwt.encode(
        claims() if payload is None else payload,
        keys.private,
        algorithm="ES256",
        headers={"kid": keys.kid if kid is None else kid},
    )


def meta(step: int = 3, key: str = "a" * 64) -> dict[str, Any]:
    return {"agentplatform/step": step, "agentplatform/idempotency_key": key}


class LiveGateway:
    """The real app on 127.0.0.1 and a free port, in a thread."""

    def __init__(self, keys: Keys, *connectors: FakeConnector) -> None:
        verifier = RunTokenVerifier(keys.public_pem, CONTRACTS)
        app = create_app(Registry(connectors), verifier, CONTRACTS)
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self._server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning")
        )
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    def __enter__(self) -> "LiveGateway":
        self._thread.start()
        deadline = time.time() + 10
        while not self._server.started:
            if time.time() > deadline:
                raise RuntimeError("gateway did not start")
            time.sleep(0.02)
        return self

    def __exit__(self, *exc: object) -> None:
        self._server.should_exit = True
        self._thread.join(5)


@contextlib.asynccontextmanager
async def mcp_client(gateway: LiveGateway, bearer: str | None):
    headers = {} if bearer is None else {"Authorization": f"Bearer {bearer}"}
    async with (
        httpx2.AsyncClient(headers=headers, timeout=httpx2.Timeout(10)) as http,
        Client(
            streamable_http_client(f"{gateway.url}/mcp", http_client=http), raise_exceptions=True
        ) as client,
    ):
        yield client


def b64_body(pem: str) -> str:
    return "".join(line for line in pem.splitlines() if not line.startswith("-----"))
