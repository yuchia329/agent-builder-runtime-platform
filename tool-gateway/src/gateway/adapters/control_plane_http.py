"""ControlPlane over HTTP: GET {CONTROL_PLANE_URL}/internal/runs/{id}.

5xx answers and network errors are retried (3 attempts); 404 is RunNotFound; anything else is
ControlPlaneUnavailable naming the status. On AWS every internal call is signed with SigV4 and
checked by API Gateway (ADR 0007); the `signer` hook is where M6 plugs that in. Locally nothing
checks, so requests go unsigned. The answer carries a fresh run token, which is never logged or
kept.
"""

import time
import urllib.parse
from collections.abc import Callable, Mapping
from typing import Any

import httpx2

from gateway.ports.control_plane import ControlPlaneUnavailable, RunNotFound

ATTEMPTS = 3
TIMEOUT_SECONDS = 5.0

# (method, url, headers, body) -> headers to send, including the signature.
Signer = Callable[[str, str, Mapping[str, str], bytes], dict[str, str]]


class HttpControlPlane:
    def __init__(
        self,
        base_url: str,
        http: httpx2.Client | None = None,
        signer: Signer | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._http = http or httpx2.Client(timeout=TIMEOUT_SECONDS)
        self._signer = signer
        self._sleep = sleep

    def get_run(self, run_id: str) -> dict[str, Any]:
        url = f"{self._base_url}/internal/runs/{urllib.parse.quote(run_id, safe='')}"
        headers = {"Accept": "application/json"}
        if self._signer is not None:
            headers = self._signer("GET", url, headers, b"")
        for attempt in range(1, ATTEMPTS + 1):
            try:
                response = self._http.get(url, headers=headers)
            except httpx2.TransportError:
                if attempt == ATTEMPTS:
                    raise ControlPlaneUnavailable("control plane unreachable") from None
                self._sleep(attempt)
                continue
            if response.status_code >= 500 and attempt < ATTEMPTS:
                self._sleep(attempt)
                continue
            break
        if response.status_code == 404:
            raise RunNotFound(run_id)
        if response.status_code != 200:
            raise ControlPlaneUnavailable(f"control plane answered {response.status_code}")
        try:
            run = response.json()
        except ValueError:
            raise ControlPlaneUnavailable("control plane answer is not JSON") from None
        if not isinstance(run, dict):
            raise ControlPlaneUnavailable("control plane answer is not a run")
        return run
