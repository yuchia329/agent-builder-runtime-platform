"""The platform's public API, as the driver uses it (tenant API key in `X-Api-Key`).

429 and 5xx answers are retried (API Gateway throttling, Lambda limits), except a 429 whose
problem `reason` is `tenant_budget`: the tenant is over its daily spend cap (ADR 0026), an answer
that does not change by retrying. It raises ApiError with that reason at once."""

import asyncio
import json
from dataclasses import dataclass, field

import httpx

RETRY_STATUSES = frozenset({429, 502, 503, 504})
TENANT_BUDGET = "tenant_budget"


class ApiError(Exception):
    def __init__(self, method: str, path: str, status: int, body: str, reason: str | None = None):
        super().__init__(f"{method} {path} answered {status}: {body[:300]}")
        self.status = status
        self.reason = reason  # the problem's `reason`, if it had one


def _reason(response: httpx.Response) -> str | None:
    try:
        body = response.json()
    except ValueError:
        return None
    return body.get("reason") if isinstance(body, dict) else None


@dataclass
class Retries:
    """How many requests were answered with a retryable status, by status (throttle 429,
    Lambda limit 503, ...). A load test reports them: they are limits being hit."""

    by_status: dict[int, int] = field(default_factory=dict)

    def add(self, status: int) -> None:
        self.by_status[status] = self.by_status.get(status, 0) + 1


class Api:
    def __init__(
        self,
        client: httpx.AsyncClient,
        key: str,
        *,
        attempts: int = 4,
        backoff_s: float = 1.0,
        sleep=asyncio.sleep,
    ) -> None:
        self._client = client
        self._key = key
        self._attempts = attempts
        self._backoff_s = backoff_s
        self._sleep = sleep
        self.retries = Retries()

    async def create_agent(self, name: str) -> bool:
        """True if made, False if it already existed."""
        status, _ = await self._call("POST", "/agents", json_body={"name": name}, ok=(201, 409))
        return status == 201

    async def publish(self, name: str, spec_yaml: bytes) -> int:
        _, body = await self._call(
            "POST",
            f"/agents/{name}/versions",
            content=spec_yaml,
            content_type="application/yaml",
        )
        return body["version"]

    async def start_run(self, agent: str, run_input: dict) -> dict:
        _, body = await self._call("POST", "/runs", json_body={"agent": agent, "input": run_input})
        return body

    async def get_run(self, run_id: str) -> dict:
        _, body = await self._call("GET", f"/runs/{run_id}")
        return body

    async def _call(
        self,
        method: str,
        path: str,
        *,
        json_body: dict | None = None,
        content: bytes | None = None,
        content_type: str = "application/json",
        ok: tuple[int, ...] = (200, 201),
    ) -> tuple[int, dict]:
        if json_body is not None:
            content = json.dumps(json_body).encode()
        headers = {"X-Api-Key": self._key, "Content-Type": content_type}
        for attempt in range(1, self._attempts + 1):
            response = await self._client.request(method, path, content=content, headers=headers)
            if response.status_code in ok:
                return response.status_code, (response.json() if response.content else {})
            reason = _reason(response) if response.status_code >= 400 else None
            if response.status_code == 429 and reason == TENANT_BUDGET:
                raise ApiError(method, path, 429, response.text, reason)
            if response.status_code in RETRY_STATUSES:
                self.retries.add(response.status_code)
                if attempt < self._attempts:
                    await self._sleep(self._backoff_s * 2 ** (attempt - 1))
                    continue
            raise ApiError(method, path, response.status_code, response.text, reason)
        raise AssertionError("unreachable")
