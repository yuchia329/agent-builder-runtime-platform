"""Domain types for the worker core. Plain dataclasses; no AWS or vendor imports.

Run and spec shapes follow contracts/internal-api.openapi.yaml and contracts/agent-spec.schema.json.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class RunStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class FailureReason(StrEnum):
    BUDGET_STEPS = "budget_steps"
    BUDGET_USD = "budget_usd"
    BUDGET_SECONDS = "budget_seconds"
    APPROVAL_REJECTED = "approval_rejected"
    ERROR = "error"


@dataclass(frozen=True)
class Budget:
    max_steps: int
    max_usd: float
    max_seconds: int


@dataclass(frozen=True)
class AgentSpec:
    name: str
    model: str
    instructions: str
    trigger: str
    acts_as: str
    tools: tuple[str, ...]
    require_approval: frozenset[str]
    budget: Budget

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AgentSpec:
        b = d["budget"]
        return cls(
            name=d["name"],
            model=d["model"],
            instructions=d["instructions"],
            trigger=d["trigger"],
            acts_as=d["acts_as"],
            tools=tuple(d["tools"]),
            require_approval=frozenset(d.get("require_approval", [])),
            budget=Budget(b["max_steps"], b["max_usd"], b["max_seconds"]),
        )


class Decision(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass(frozen=True)
class ApprovalDecision:
    """The latest human decision on a pending call. Set by the control plane only."""

    step: int
    tool: str
    arguments: dict[str, Any]
    decision: Decision
    decided_by: str
    decided_at: str

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ApprovalDecision:
        return cls(
            step=d["step"],
            tool=d["tool"],
            arguments=d["arguments"],
            decision=Decision(d["decision"]),
            decided_by=d["decided_by"],
            decided_at=d["decided_at"],
        )


@dataclass(frozen=True)
class RunRecord:
    """A run as returned by GET /internal/runs/{run_id}."""

    run_id: str
    tenant_id: str
    agent_name: str
    agent_version: int
    status: RunStatus
    steps: int
    cost_usd: float
    spec: AgentSpec
    input: dict[str, Any]
    approval: ApprovalDecision | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RunRecord:
        approval = d.get("approval")
        return cls(
            run_id=d["run_id"],
            tenant_id=d["tenant_id"],
            agent_name=d["agent_name"],
            agent_version=d["agent_version"],
            status=RunStatus(d["status"]),
            steps=d["steps"],
            cost_usd=d["cost_usd"],
            spec=AgentSpec.from_dict(d["spec"]),
            input=d["input"],
            approval=None if approval is None else ApprovalDecision.from_dict(approval),
        )


@dataclass(frozen=True)
class PendingApproval:
    step: int
    tool: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class Failure:
    reason: FailureReason
    message: str


@dataclass(frozen=True)
class RunUpdate:
    """Body of PATCH /internal/runs/{run_id}. None fields are left out."""

    status: RunStatus
    steps: int | None = None
    cost_usd: float | None = None
    trace_id: str | None = None
    pending_approval: PendingApproval | None = None
    failure: Failure | None = None
    output: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"status": str(self.status)}
        if self.steps is not None:
            d["steps"] = self.steps
        if self.cost_usd is not None:
            d["cost_usd"] = self.cost_usd
        if self.trace_id is not None:
            d["trace_id"] = self.trace_id
        if self.pending_approval is not None:
            p = self.pending_approval
            d["pending_approval"] = {"step": p.step, "tool": p.tool, "arguments": p.arguments}
        if self.failure is not None:
            d["failure"] = {"reason": str(self.failure.reason), "message": self.failure.message}
        if self.output is not None:
            d["output"] = self.output
        return d


# --- Model conversation ---------------------------------------------------------------------


@dataclass(frozen=True)
class ToolSpec:
    """A tool as offered to the model.

    ``read_only`` (changes nothing) and ``idempotent`` (doing it twice has the same effect as
    once) follow MCP's tool annotations (readOnlyHint, idempotentHint). Either makes a call safe
    to repeat when a crash leaves its outcome unknown; a tool with neither is never repeated.
    """

    name: str
    description: str
    input_schema: dict[str, Any]
    read_only: bool = False
    idempotent: bool = False

    @property
    def safe_to_repeat(self) -> bool:
        return self.read_only or self.idempotent


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ToolResult:
    call_id: str
    content: Any
    is_error: bool = False


class StopReason(StrEnum):
    END_TURN = "end_turn"  # final answer
    TOOL_USE = "tool_use"  # wants tool calls
    MAX_TOKENS = "max_tokens"  # cut off
    REFUSAL = "refusal"  # declined


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class ModelTurn:
    """One model reply.

    ``provider_data`` is opaque to the core: an adapter stores what its provider needs to see
    again in later turns (for example the model's reasoning) and reads it back from the history.
    It must stay JSON-serializable so it can be checkpointed.
    """

    stop_reason: StopReason
    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    usage: Usage = field(default_factory=Usage)
    provider_data: dict[str, Any] | None = None


@dataclass(frozen=True)
class Message:
    """One entry in the conversation sent to the model."""

    role: str  # "user" | "assistant" | "tool"
    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    tool_result: ToolResult | None = None
    provider_data: dict[str, Any] | None = None  # assistant only; see ModelTurn

    @classmethod
    def user(cls, text: str) -> Message:
        return cls(role="user", text=text)

    @classmethod
    def assistant(cls, turn: ModelTurn) -> Message:
        return cls(
            role="assistant",
            text=turn.text,
            tool_calls=turn.tool_calls,
            provider_data=turn.provider_data,
        )

    @classmethod
    def tool(cls, result: ToolResult) -> Message:
        return cls(role="tool", tool_result=result)

    def to_json(self) -> dict[str, Any]:
        """A JSON-serializable dict (checkpoints); empty fields left out. See from_json."""
        d: dict[str, Any] = {"role": self.role}
        if self.text:
            d["text"] = self.text
        if self.tool_calls:
            d["tool_calls"] = [
                {"id": c.id, "name": c.name, "arguments": c.arguments} for c in self.tool_calls
            ]
        if self.tool_result is not None:
            r = self.tool_result
            d["tool_result"] = {"call_id": r.call_id, "content": r.content, "is_error": r.is_error}
        if self.provider_data is not None:
            d["provider_data"] = self.provider_data
        return d

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> Message:
        r = d.get("tool_result")
        return cls(
            role=d["role"],
            text=d.get("text", ""),
            tool_calls=tuple(
                ToolCall(c["id"], c["name"], c["arguments"]) for c in d.get("tool_calls", [])
            ),
            tool_result=None
            if r is None
            else ToolResult(r["call_id"], r["content"], bool(r.get("is_error", False))),
            provider_data=d.get("provider_data"),
        )
