"""What the runner stands up around one process: run tokens, the internal run endpoint the gateway's
approval check reads, and the real tool gateway as a subprocess pointed at the simulators.

The gateway's environment is built from scratch: nothing from `.env` reaches it (no real GitHub
App key, OAuth secret or token). It gets a throwaway P-256 run-token key pair, a throwaway GitHub
App key in a temporary file, and `CONNECTOR_GITHUB_BASE_URL` pointing at the simulator.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from worker.adapters.control_plane_memory import InMemoryControlPlane
from worker.core.errors import RunNotFound

from evals.simulators.github import REPO
from evals.task import REPO_ROOT

GATEWAY_PROJECT = REPO_ROOT / "tool-gateway"
TENANT = "tenant-eval"
PRINCIPAL = "api:tenant-eval"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class RunTokens:
    """Signs run tokens (contracts/run-token.schema.json) like the control plane, with a key pair
    made for this process."""

    def __init__(self) -> None:
        self._private = ec.generate_private_key(ec.SECP256R1())
        public = self._private.public_key()
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
        self._kid = _b64(hashlib.sha256(canonical).digest())  # RFC 7638 thumbprint

    def sign(self, run: dict[str, Any]) -> str:
        spec = run["spec"]
        now = int(time.time())
        claims = {
            "iss": "control-plane",
            "aud": "tool-gateway",
            "sub": run["run_id"],
            "tenant": run["tenant_id"],
            "agent": run["agent_name"],
            "agent_version": run["agent_version"],
            "acts_as": spec["acts_as"],
            "owner": PRINCIPAL,
            "started_by": PRINCIPAL,
            "tools": spec["tools"],
            "require_approval": spec.get("require_approval", []),
            "iat": now,
            "exp": now + spec["budget"]["max_seconds"] + 60,
        }
        return jwt.encode(claims, self._private, algorithm="ES256", headers={"kid": self._kid})


class InternalRunServer:
    """GET /internal/runs/{id} from an InMemoryControlPlane, on loopback: what the gateway's
    second approval check reads (contracts/internal-api.openapi.yaml)."""

    def __init__(self, control_plane: InMemoryControlPlane) -> None:
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def do_GET(self) -> None:  # noqa: N802 - http.server's name
                prefix = "/internal/runs/"
                status, body = 404, {"message": "not found"}
                if self.path.startswith(prefix):
                    with contextlib.suppress(KeyError, RunNotFound):
                        status, body = (
                            200,
                            control_plane.run_json(unquote(self.path[len(prefix) :])),
                        )
                payload = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"
        self._thread = threading.Thread(
            target=self._server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        )

    def __enter__(self) -> InternalRunServer:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._server.shutdown()
        self._server.server_close()


class GatewayProcess:
    """`uv run gateway` in ../tool-gateway with the GitHub connector pointed at the simulator.
    Its log goes to a file in a temporary folder, named in startup errors."""

    def __init__(self, tokens: RunTokens, github_url: str, control_plane_url: str) -> None:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self._tokens = tokens
        self._github_url = github_url
        self._control_plane_url = control_plane_url
        self._folder = tempfile.TemporaryDirectory(prefix="evals-gateway-")
        self.log_file = Path(self._folder.name) / "gateway.log"
        self._process: subprocess.Popen[bytes] | None = None

    def _env(self) -> dict[str, str]:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = Path(self._folder.name) / "app.pem"
        pem.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.NoEncryption(),
            )
        )
        pem.chmod(0o600)
        env = {
            k: os.environ[k] for k in ("PATH", "HOME", "TMPDIR", "UV_CACHE_DIR") if k in os.environ
        }
        return env | {
            "RUN_TOKEN_PUBLIC_KEY": self._tokens.public_pem,
            "CONNECTORS": "github",
            "GITHUB_REPOS": REPO,
            "TENANT_ID": TENANT,
            "GITHUB_APP_ID": "1",
            "GITHUB_APP_INSTALLATION_ID": "1",
            "GITHUB_APP_PRIVATE_KEY_FILE": str(pem),
            "CONNECTOR_GITHUB_BASE_URL": self._github_url,
            "CONTROL_PLANE_URL": self._control_plane_url,
            "GATEWAY_PORT": str(self.port),
        }

    def __enter__(self) -> GatewayProcess:
        uv = shutil.which("uv")
        if uv is None:
            raise RuntimeError("uv is needed to run the tool gateway")
        log = self.log_file.open("wb")
        self._process = subprocess.Popen(
            [uv, "run", "--project", str(GATEWAY_PROJECT), "gateway"],
            cwd=GATEWAY_PROJECT,
            env=self._env(),
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        log.close()
        deadline = time.time() + 90
        while time.time() < deadline:
            if self._process.poll() is not None:
                self._stop()
                raise RuntimeError(f"tool gateway exited at startup; log: {self.log_file}")
            try:
                if httpx.get(f"{self.url}/health", timeout=1).status_code == 200:
                    return self
            except httpx.TransportError:
                time.sleep(0.1)
        self._stop()
        raise RuntimeError(f"tool gateway did not start; log: {self.log_file}")

    def _stop(self) -> None:
        if self._process is not None and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(10)
            except subprocess.TimeoutExpired:
                self._process.kill()

    def __exit__(self, *exc: object) -> None:
        self._stop()
        self._folder.cleanup()
