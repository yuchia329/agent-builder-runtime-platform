"""ControlPlane over HTTP: the internal run API (contracts/internal-api.openapi.yaml).

Every request is signed with AWS SigV4 for service `execute-api`; in AWS, API Gateway checks the
signature (ADR 0007). Locally the control plane ignores it, and the fake .env credentials sign.
Connection errors, 429 and 5xx are retried with backoff, then raised as ControlPlaneError.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import quote

import boto3
import httpx
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.credentials import Credentials

from worker.core.errors import ControlPlaneError, RunConflict, RunNotFound
from worker.core.types import RunRecord, RunUpdate

log = logging.getLogger(__name__)

RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})

# (method, url, headers, body) -> headers to send, including the signature.
Signer = Callable[[str, str, Mapping[str, str], bytes], dict[str, str]]


def sigv4_signer(credentials: Callable[[], Credentials], region: str) -> Signer:
    """Signs with fresh credentials on every request (task role credentials rotate)."""

    def sign(method: str, url: str, headers: Mapping[str, str], body: bytes) -> dict[str, str]:
        request = AWSRequest(method=method, url=url, data=body, headers=dict(headers))
        SigV4Auth(credentials(), "execute-api", region).add_auth(request)
        return dict(request.headers.items())

    return sign


def default_signer(region: str) -> Signer:
    session = boto3.Session()

    def credentials() -> Credentials:
        creds = session.get_credentials()
        if creds is None:
            raise ControlPlaneError("no AWS credentials found to sign control plane requests")
        return creds.get_frozen_credentials()

    return sigv4_signer(credentials, region)


class HttpControlPlane:
    def __init__(
        self,
        base_url: str,
        signer: Signer,
        *,
        attempts: int = 3,
        backoff_seconds: float = 0.5,
        timeout_seconds: float = 10.0,
        sleep: Callable[[float], None] = time.sleep,
        trace_headers: Callable[[], Mapping[str, str]] = dict,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._sign = signer
        self._trace_headers = trace_headers  # `traceparent` of the current span (ADR 0022)
        self._attempts = attempts
        self._backoff = backoff_seconds
        self._sleep = sleep
        self._http = httpx.Client(timeout=timeout_seconds)

    def close(self) -> None:
        self._http.close()

    def get_run(self, run_id: str) -> RunRecord:
        response = self._request("GET", run_id)
        if response.status_code == 404:
            raise RunNotFound(run_id)
        self._expect_ok(response)
        return RunRecord.from_dict(response.json())

    def update_run(self, run_id: str, update: RunUpdate) -> None:
        response = self._request("PATCH", run_id, update.to_dict())
        if response.status_code == 404:
            raise RunNotFound(run_id)
        if response.status_code == 409:
            raise RunConflict(run_id, update.status, _detail(response))
        self._expect_ok(response)

    def _request(self, method: str, run_id: str, body: dict[str, Any] | None = None):
        url = f"{self._base}/internal/runs/{quote(run_id, safe='')}"
        content = b"" if body is None else json.dumps(body).encode()
        headers = {"Accept": "application/json", **self._trace_headers()}
        if body is not None:
            headers["Content-Type"] = "application/json"

        problem = ""
        for attempt in range(1, self._attempts + 1):
            # Sign each attempt: the signature includes a timestamp.
            signed = self._sign(method, url, headers, content)
            try:
                response = self._http.request(method, url, content=content, headers=signed)
            except httpx.TransportError as e:
                problem = f"{type(e).__name__}: {e}"
            else:
                if response.status_code not in RETRY_STATUSES:
                    return response
                problem = f"HTTP {response.status_code}: {_detail(response)}"
            if attempt < self._attempts:
                log.warning("%s %s failed (%s); retrying", method, url, problem)
                self._sleep(self._backoff * 2 ** (attempt - 1))
        raise ControlPlaneError(f"{method} {url} failed after {self._attempts} attempts: {problem}")

    @staticmethod
    def _expect_ok(response: httpx.Response) -> None:
        if not 200 <= response.status_code < 300:
            raise ControlPlaneError(
                f"{response.request.method} {response.request.url} returned "
                f"{response.status_code}: {_detail(response)}"
            )


def _detail(response: httpx.Response) -> str:
    """The problem+json `detail`, or the start of the body."""
    try:
        return str(response.json().get("detail", ""))
    except (ValueError, AttributeError):
        return response.text[:200]
