"""Model adapter for the OpenAI Chat Completions wire format (ADR 0010).

Serves every provider that speaks it: MiniMax, OpenAI, OpenRouter, Together AI, Gemini's
OpenAI-compatible endpoint, vLLM, Ollama, llama.cpp. Plain httpx, no vendor SDK, so retries stay
under our control.

Translation rules:

- Tool names: OpenAI function names allow ``[a-zA-Z0-9_-]`` only, ours are
  ``connector.operation``. Ours never contain '-', so '.' <-> '-' is lossless.
- A failed tool result is sent as content starting with ``Error:`` (the format has no error flag).
- ``reasoning_content`` (MiniMax with ``reasoning_split``, DeepSeek, others) is kept in
  ``provider_data`` and sent back with the assistant message, as those providers ask.
- A reply without token usage is an error: without it the dollar budget cannot be enforced.

Retries: throttling (429, MiniMax rate-limit codes), server errors (5xx, MiniMax server codes) and
network errors or timeouts are retried with exponential backoff and jitter, honoring
``Retry-After`` up to ``MAX_RETRY_AFTER``; then ``ModelError``, or ``ModelRateLimited`` when
the last attempt was rate limited (429 or a MiniMax rate-limit code), so the loop sets the run
aside instead of failing it (ADR 0027). Everything else (bad key, no
balance, invalid request, unreadable reply) fails at once. A request that timed out may still
have been billed by the provider.
"""

from __future__ import annotations

import json
import logging
import random
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import httpx

from worker.core.errors import ModelError, ModelRateLimited
from worker.core.types import Message, ModelTurn, StopReason, ToolCall, ToolSpec, Usage

log = logging.getLogger(__name__)

MAX_ERROR_BODY = 500
MAX_RETRY_AFTER = 30.0
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
# MiniMax base_resp codes worth retrying (platform.minimax.io/docs/api-reference/errorcode,
# checked 2026-11-16, rate limits again 2026-11-21): rate limits, timeouts, server errors.
RATE_LIMIT_PROVIDER_CODES = frozenset({1002, 1039, 1041, 1045, 2045, 2046, 2047})
RETRY_PROVIDER_CODES = RATE_LIMIT_PROVIDER_CODES | {1001, 2066, 1000, 1024, 1033, 2064}


class _Retryable(ModelError):
    def __init__(
        self, message: str, retry_after: float | None = None, *, rate_limited: bool = False
    ) -> None:
        super().__init__(message)
        self.retry_after = retry_after
        self.rate_limited = rate_limited


def wire_tool_name(name: str) -> str:
    return name.replace(".", "-")


def tool_name_from_wire(name: str) -> str:
    return name.replace("-", ".")


class OpenAICompatModel:
    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        *,
        max_tokens: int = 4096,
        extra_body: Mapping[str, Any] | None = None,
        timeout_seconds: float = 120.0,
        attempts: int = 4,
        backoff_seconds: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
        client: httpx.Client | None = None,
    ) -> None:
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._max_tokens = max_tokens
        self._extra_body = dict(extra_body or {})
        self._attempts = attempts
        self._backoff = backoff_seconds
        self._sleep = sleep
        self._http = client or httpx.Client(timeout=timeout_seconds)

    def complete(
        self, model: str, system: str, messages: Sequence[Message], tools: Sequence[ToolSpec]
    ) -> ModelTurn:
        body: dict[str, Any] = {
            **self._extra_body,
            "model": model,
            "messages": [{"role": "system", "content": system}]
            + [_wire_message(m) for m in messages],
            "max_completion_tokens": self._max_tokens,
        }
        if tools:
            body["tools"] = [_wire_tool(t) for t in tools]

        for attempt in range(1, self._attempts + 1):
            try:
                return self._send(body)
            except _Retryable as e:
                if attempt == self._attempts:
                    message = f"{e} (gave up after {attempt} attempts)"
                    if e.rate_limited:
                        raise ModelRateLimited(message, e.retry_after) from e
                    raise ModelError(message) from e
                delay = self._delay(attempt, e.retry_after)
                log.warning(
                    "model call attempt %d failed: %s; retrying in %.1fs", attempt, e, delay
                )
                self._sleep(delay)
        raise AssertionError("unreachable")

    def _delay(self, attempt: int, retry_after: float | None) -> float:
        if retry_after is not None:
            return min(retry_after, MAX_RETRY_AFTER)
        return self._backoff * 2 ** (attempt - 1) * random.uniform(0.75, 1.25)

    def _send(self, body: dict[str, Any]) -> ModelTurn:
        try:
            response = self._http.post(self._url, json=body, headers=self._headers)
        except httpx.TransportError as e:  # connection refused, reset, timeouts
            # The exception text holds the URL, never the Authorization header.
            raise _Retryable(f"Model request failed: {type(e).__name__}: {e}") from e
        except httpx.HTTPError as e:
            raise ModelError(f"Model request failed: {type(e).__name__}: {e}") from e
        if response.status_code != 200:
            message = (
                f"Model provider returned HTTP {response.status_code}: "
                f"{response.text[:MAX_ERROR_BODY]}"
            )
            if response.status_code in RETRY_STATUSES:
                raise _Retryable(
                    message, _retry_after(response), rate_limited=response.status_code == 429
                )
            raise ModelError(message)
        try:
            return _parse_turn(response.json())
        except (ValueError, KeyError, IndexError, TypeError) as e:
            raise ModelError(f"Unexpected model reply: {type(e).__name__}: {e}") from e


def _retry_after(response: httpx.Response) -> float | None:
    """Seconds from a Retry-After header; HTTP-date values fall back to our backoff."""
    try:
        return max(0.0, float(response.headers["Retry-After"]))
    except (KeyError, ValueError):
        return None


def _wire_tool(tool: ToolSpec) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": wire_tool_name(tool.name),
            "description": tool.description,
            "parameters": tool.input_schema,
        },
    }


def _wire_message(m: Message) -> dict[str, Any]:
    if m.role == "user":
        return {"role": "user", "content": m.text}
    if m.role == "assistant":
        wire: dict[str, Any] = {"role": "assistant", "content": m.text}
        if m.tool_calls:
            wire["tool_calls"] = [
                {
                    "id": c.id,
                    "type": "function",
                    "function": {
                        "name": wire_tool_name(c.name),
                        "arguments": json.dumps(c.arguments),
                    },
                }
                for c in m.tool_calls
            ]
        if m.provider_data and m.provider_data.get("reasoning_content"):
            wire["reasoning_content"] = m.provider_data["reasoning_content"]
        return wire
    if m.role == "tool" and m.tool_result is not None:
        r = m.tool_result
        content = r.content if isinstance(r.content, str) else json.dumps(r.content)
        return {
            "role": "tool",
            "tool_call_id": r.call_id,
            "content": f"Error: {content}" if r.is_error else content,
        }
    raise ValueError(f"Cannot send message with role {m.role!r}")


def _parse_turn(data: dict[str, Any]) -> ModelTurn:
    # MiniMax can answer HTTP 200 with the error in base_resp (bad key, no balance, ...).
    base = data.get("base_resp") or {}
    code = base.get("status_code", 0)
    if code != 0:
        message = f"Model provider error {code}: {base.get('status_msg', '')}"
        if code in RETRY_PROVIDER_CODES:
            raise _Retryable(message, rate_limited=code in RATE_LIMIT_PROVIDER_CODES)
        raise ModelError(message)
    choice = data["choices"][0]
    message = choice["message"]
    finish = choice.get("finish_reason")

    usage = data.get("usage")
    if not usage or "prompt_tokens" not in usage or "completion_tokens" not in usage:
        raise ModelError("Model reply has no token usage, so the dollar budget cannot be enforced")

    calls = tuple(_parse_call(c) for c in message.get("tool_calls") or ())
    if message.get("refusal") or finish == "content_filter":
        stop = StopReason.REFUSAL
    elif finish == "length":
        stop = StopReason.MAX_TOKENS
    elif calls:
        stop = StopReason.TOOL_USE  # some providers say "stop" even with tool calls
    else:
        stop = StopReason.END_TURN

    reasoning = message.get("reasoning_content")
    return ModelTurn(
        stop,
        text=message.get("content") or "",
        tool_calls=calls,
        usage=Usage(int(usage["prompt_tokens"]), int(usage["completion_tokens"])),
        provider_data={"reasoning_content": reasoning} if reasoning else None,
    )


def _parse_call(call: dict[str, Any]) -> ToolCall:
    function = call["function"]
    raw = function.get("arguments") or "{}"
    try:
        arguments = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ModelError(f"Model sent tool arguments that are not JSON: {raw[:200]}") from e
    if not isinstance(arguments, dict):
        raise ModelError(f"Model sent tool arguments that are not an object: {raw[:200]}")
    return ToolCall(call["id"], tool_name_from_wire(function["name"]), arguments)
