"""Model adapter for the Amazon Bedrock Converse API (ADR 0009, ADR 0010).

One API for every Bedrock model (Amazon Nova, Anthropic Claude, MiniMax, Llama, ...); plain boto3
``bedrock-runtime`` ``converse``, no Strands, since the loop is ours.

Translation rules:

- Tool names: Bedrock allows ``[a-zA-Z0-9_-]``; ours are ``connector.operation``. '.' becomes
  '_' (not '-': Amazon Nova fails with ``ModelErrorException: Model produced invalid sequence as
  part of ToolUse`` on hyphenated names). '_' also appears inside operation names, so replies are
  mapped back through the tool list of the request, not by string replace.
- Consecutive tool results become one user message with several ``toolResult`` blocks: Converse
  needs strict user/assistant alternation.
- A failed tool result is sent as text starting with ``Error:``. Converse's ``status`` field is
  not used: only some models accept it.
- Empty text is never sent (Converse rejects blank text blocks).
- Amazon Nova writes ``<thinking>...</thinking>`` before its answer and may wrap the answer in
  ``<response>...</response>``; the thinking is dropped and the answer unwrapped, so run output
  and history hold the answer only.
- ``reasoningContent`` blocks (models with extended thinking) are kept in ``provider_data`` and
  sent back first in the assistant message, as those models require.
- ``extra_body`` (``MODEL_<NAME>_EXTRA_BODY``) becomes ``additionalModelRequestFields``;
  ``temperature`` (``MODEL_<NAME>_TEMPERATURE``) goes into ``inferenceConfig``. For Amazon Nova,
  AWS's tool-use troubleshooting guide recommends greedy decoding: temperature 0 and extra body
  ``{"inferenceConfig": {"topK": 1}}``.

Credentials come from boto3's usual chain, or from a named profile when ``profile`` is set. A
named profile ignores AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY, so the fake keys the local stack
puts in .env do not leak into Bedrock calls. The client is created on first use, so a worker
without that profile (for example in Docker) still starts; only runs on this provider fail.

Retries: botocore's standard mode (throttling, 5xx, connection errors) with ``attempts`` tries,
then ``ModelError``; ``ModelRateLimited`` if Bedrock was still throttling
(``ThrottlingException``), so the loop sets the run aside (ADR 0027). Errors never include
credentials.
"""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Mapping, Sequence
from typing import Any

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

from worker.core.errors import ModelError, ModelRateLimited
from worker.core.types import Message, ModelTurn, StopReason, ToolCall, ToolSpec, Usage

THINKING = re.compile(r"<thinking>.*?</thinking>", re.DOTALL)
RESPONSE = re.compile(r"<response>(.*?)</response>", re.DOTALL)

RATE_LIMIT_CODES = frozenset({"ThrottlingException"})

STOP_REASONS = {
    "end_turn": StopReason.END_TURN,
    "stop_sequence": StopReason.END_TURN,
    "tool_use": StopReason.TOOL_USE,
    "max_tokens": StopReason.MAX_TOKENS,
    "guardrail_intervened": StopReason.REFUSAL,
    "content_filtered": StopReason.REFUSAL,
}


class BedrockConverseModel:
    def __init__(
        self,
        *,
        region: str | None = None,
        profile: str | None = None,
        max_tokens: int = 4096,
        temperature: float | None = None,
        extra_body: Mapping[str, Any] | None = None,
        attempts: int = 4,
        timeout_seconds: float = 120.0,
        client: Any = None,
    ) -> None:
        self._region = region
        self._profile = profile
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._extra = dict(extra_body or {})
        self._boto_config = BotoConfig(
            retries={"mode": "standard", "max_attempts": attempts},
            read_timeout=timeout_seconds,
            connect_timeout=10,
        )
        self._client = client
        self._lock = threading.Lock()

    def complete(
        self, model: str, system: str, messages: Sequence[Message], tools: Sequence[ToolSpec]
    ) -> ModelTurn:
        request: dict[str, Any] = {
            "modelId": model,
            "messages": _wire_messages(messages),
            "inferenceConfig": {"maxTokens": self._max_tokens},
        }
        if self._temperature is not None:  # 0 is a real value: compare with None
            request["inferenceConfig"]["temperature"] = self._temperature
        if system:
            request["system"] = [{"text": system}]
        if tools:
            request["toolConfig"] = {"tools": [_wire_tool(t) for t in tools]}
        names = {wire_tool_name(t.name): t.name for t in tools}
        if self._extra:
            request["additionalModelRequestFields"] = self._extra
        try:
            response = self._bedrock().converse(**request)
        except ClientError as e:
            error = e.response.get("Error", {})
            message = (
                f"Bedrock returned {error.get('Code', 'an error')}: {error.get('Message', '')}"
            )
            if error.get("Code") in RATE_LIMIT_CODES:
                raise ModelRateLimited(message) from e
            raise ModelError(message) from e
        except BotoCoreError as e:  # no credentials, unknown profile, connection failures
            raise ModelError(f"Bedrock request failed: {type(e).__name__}: {e}") from e
        try:
            return _parse_turn(response, names)
        except (KeyError, TypeError, ValueError) as e:
            raise ModelError(f"Unexpected Bedrock reply: {type(e).__name__}: {e}") from e

    def _bedrock(self) -> Any:
        with self._lock:
            if self._client is None:
                session = boto3.Session(profile_name=self._profile, region_name=self._region)
                self._client = session.client("bedrock-runtime", config=self._boto_config)
            return self._client


def wire_tool_name(name: str) -> str:
    return name.replace(".", "_")


def _wire_tool(tool: ToolSpec) -> dict[str, Any]:
    return {
        "toolSpec": {
            "name": wire_tool_name(tool.name),
            "description": tool.description,
            "inputSchema": {"json": tool.input_schema},
        }
    }


def _wire_messages(messages: Sequence[Message]) -> list[dict[str, Any]]:
    wire: list[dict[str, Any]] = []
    for m in messages:
        role = "assistant" if m.role == "assistant" else "user"
        blocks = _blocks(m)
        if wire and wire[-1]["role"] == role:
            wire[-1]["content"].extend(blocks)  # tool results of one turn share a message
        else:
            wire.append({"role": role, "content": blocks})
    return wire


def _blocks(m: Message) -> list[dict[str, Any]]:
    if m.role == "user":
        return [{"text": m.text or "(empty)"}]
    if m.role == "assistant":
        blocks: list[dict[str, Any]] = []
        if m.provider_data:
            blocks.extend(m.provider_data.get("reasoning", []))
        if m.text:
            blocks.append({"text": m.text})
        blocks.extend(
            {"toolUse": {"toolUseId": c.id, "name": wire_tool_name(c.name), "input": c.arguments}}
            for c in m.tool_calls
        )
        return blocks or [{"text": "(no text)"}]
    if m.role == "tool" and m.tool_result is not None:
        r = m.tool_result
        content = r.content if isinstance(r.content, str) else json.dumps(r.content)
        text = f"Error: {content}" if r.is_error else (content or "(empty)")
        return [{"toolResult": {"toolUseId": r.call_id, "content": [{"text": text}]}}]
    raise ValueError(f"Cannot send message with role {m.role!r}")


def _parse_turn(response: dict[str, Any], names: Mapping[str, str]) -> ModelTurn:
    """``names`` maps wire tool names back to ours; an unknown name passes through unchanged."""
    usage = response.get("usage") or {}
    if "inputTokens" not in usage or "outputTokens" not in usage:
        raise ModelError(
            "Bedrock reply has no token usage, so the dollar budget cannot be enforced"
        )
    reason = response["stopReason"]
    if reason == "model_context_window_exceeded":
        raise ModelError("The conversation is larger than the model's context window")
    blocks = response["output"]["message"]["content"]

    texts, calls, reasoning = [], [], []
    for block in blocks:
        if "text" in block:
            texts.append(block["text"])
        elif "toolUse" in block:
            use = block["toolUse"]
            if not isinstance(use.get("input"), dict):
                raise ModelError(
                    f"Model sent tool input that is not an object: {use.get('input')!r}"
                )
            name = names.get(use["name"], use["name"])
            calls.append(ToolCall(use["toolUseId"], name, use["input"]))
        elif "reasoningContent" in block:
            reasoning.append(block)

    stop = STOP_REASONS.get(reason)
    if stop is None:  # malformed_tool_use, malformed_model_output, or new reasons
        raise ModelError(f"Bedrock stopped with '{reason}'")
    if calls and stop is StopReason.END_TURN:
        stop = StopReason.TOOL_USE
    return ModelTurn(
        stop,
        text=_answer("".join(texts)),
        tool_calls=tuple(calls),
        usage=Usage(int(usage["inputTokens"]), int(usage["outputTokens"])),
        provider_data={"reasoning": reasoning} if reasoning else None,
    )


def _answer(text: str) -> str:
    """Nova's text without its <thinking> part and <response> wrapper; other text unchanged."""
    if "<thinking>" not in text and "<response>" not in text:
        return text
    text = THINKING.sub("", text)
    wrapped = RESPONSE.findall(text)
    return ("\n".join(wrapped) if wrapped else text).strip()
