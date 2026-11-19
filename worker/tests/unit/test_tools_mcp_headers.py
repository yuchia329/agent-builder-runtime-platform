"""Which headers GatewayTools sends (contracts/mcp-tool-call.schema.json): the run token in
X-Run-Token always; SigV4 in Authorization when signing (AWS), Bearer there when not (local)."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from botocore.credentials import Credentials

from worker.adapters.control_plane_http import sigv4_signer
from worker.adapters.tools_mcp import GatewayTools, SignedAuth
from worker.core.errors import ToolError
from worker.core.types import ToolContext

TOKEN = "eyJ.header.payload.signature"


class Recorder:
    """Answers every POST like a gateway refusing the token (401), after recording it."""

    def __init__(self) -> None:
        self.requests: list[tuple[dict[str, str], bytes]] = []
        recorder = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:
                pass

            def do_POST(self) -> None:
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                recorder.requests.append((dict(self.headers.items()), body))
                payload = json.dumps(
                    {"jsonrpc": "2.0", "id": None, "error": {"code": -32001, "message": "no"}}
                ).encode()
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"

    def __enter__(self) -> "Recorder":
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc) -> None:
        self._server.shutdown()


def context() -> ToolContext:
    return ToolContext(run_id="run_1", run_token=TOKEN, step=1, idempotency_key="run_1:1:abcdef")


def call(tools: GatewayTools) -> None:
    with pytest.raises(ToolError, match="refused the run token"):
        tools.describe(["github.search_issues"], context())


def test_unsigned_requests_carry_the_token_in_both_headers():
    with Recorder() as gateway:
        call(GatewayTools(gateway.url))

    headers, _ = gateway.requests[0]
    assert headers["X-Run-Token"] == TOKEN
    assert headers["Authorization"] == f"Bearer {TOKEN}"


def test_signed_requests_carry_sigv4_and_the_token_only_in_its_header():
    signer = sigv4_signer(lambda: Credentials("AKIDEXAMPLE", "secret", "session"), "us-east-1")
    with Recorder() as gateway:
        call(GatewayTools(gateway.url, auth=SignedAuth(signer)))

    headers, body = gateway.requests[0]
    authorization = headers["Authorization"]
    assert authorization.startswith("AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/")
    assert "/us-east-1/execute-api/aws4_request" in authorization
    signed = authorization.split("SignedHeaders=")[1].split(",")[0].split(";")
    assert {"host", "x-run-token", "x-amz-date"} <= set(signed)
    assert headers["X-Run-Token"] == TOKEN
    assert headers["X-Amz-Security-Token"] == "session"
    assert TOKEN not in authorization
    assert json.loads(body)["jsonrpc"] == "2.0"  # the signed body is the JSON-RPC request
