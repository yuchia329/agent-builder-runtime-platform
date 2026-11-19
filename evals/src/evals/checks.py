"""Deterministic checks (evals/task.schema.json → check): each looks at what a run did and
returns pass or fail with a short reason. Pure functions over an `Outcome`."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ToolCallRecord:
    """One call core sent to the tools: name, arguments, and whether it ended in a tool error."""

    tool: str
    arguments: dict[str, Any]
    error: str | None = None


@dataclass(frozen=True)
class Outcome:
    """What a task's run did, as the checks see it."""

    status: str
    failure: dict[str, Any] | None
    output: str | None
    steps: int
    cost_usd: float
    calls: tuple[ToolCallRecord, ...]
    approvals: tuple[dict[str, Any], ...]  # pending calls that were approved automatically
    world: dict[str, Any] = field(default_factory=dict)  # end state per simulated connector


@dataclass(frozen=True)
class CheckResult:
    kind: str
    passed: bool
    detail: str  # what was checked, and on failure what was found


def evaluate(check: dict[str, Any], outcome: Outcome) -> CheckResult:
    ((kind, value),) = check.items()
    return CheckResult(kind, *_CHECKS[kind](value, outcome))


def _status(expected: str, o: Outcome) -> tuple[bool, str]:
    found = o.status
    if o.failure:
        found += f" ({o.failure.get('reason')}: {o.failure.get('message', '')[:200]})"
    return o.status == expected, f"status {expected}" + (
        "" if o.status == expected else f"; got {found}"
    )


def _failure_reason(expected: str, o: Outcome) -> tuple[bool, str]:
    found = (o.failure or {}).get("reason")
    return found == expected, f"failure reason {expected}" + (
        "" if found == expected else f"; got {found or 'none'}"
    )


def _matching(match: dict[str, Any], calls: tuple[ToolCallRecord, ...]) -> list[ToolCallRecord]:
    wanted = match.get("arguments", {})
    return [
        c
        for c in calls
        if c.tool == match["tool"]
        and all(k in c.arguments and c.arguments[k] == v for k, v in wanted.items())
    ]


def _describe(match: dict[str, Any]) -> str:
    args = match.get("arguments")
    return match["tool"] + (f" with {args}" if args else "")


def _tool_called(match: dict[str, Any], o: Outcome) -> tuple[bool, str]:
    found = _matching(match, o.calls)
    ok = [c for c in found if c.error is None]
    detail = f"{_describe(match)} called"
    if ok:
        return True, detail
    if found:
        return False, f"{detail}; every matching call failed: {found[-1].error[:200]}"
    same_tool = [c.arguments for c in o.calls if c.tool == match["tool"]]
    return False, f"{detail}; calls of {match['tool']}: {same_tool or 'none'}"


def _tool_not_called(match: dict[str, Any], o: Outcome) -> tuple[bool, str]:
    found = _matching(match, o.calls)
    detail = f"{_describe(match)} not called"
    return not found, detail + ("" if not found else f"; called with {found[0].arguments}")


def _github(expected: dict[str, Any], o: Outcome) -> tuple[bool, str]:
    number = expected["issue"]
    issue = o.world.get("github", {}).get("issues", {}).get(number)
    if issue is None:
        return False, f"issue #{number} not in the simulated repository"
    problems = []
    labels = set(issue["labels"])
    if "labels" in expected and labels != set(expected["labels"]):
        problems.append(f"labels {sorted(labels)}, expected {sorted(expected['labels'])}")
    missing = set(expected.get("labels_include", [])) - labels
    if missing:
        problems.append(f"labels {sorted(labels)} lack {sorted(missing)}")
    unwanted = set(expected.get("labels_exclude", [])) & labels
    if unwanted:
        problems.append(f"labels {sorted(labels)} include {sorted(unwanted)}")
    for key in ("state", "state_reason", "duplicate_of"):
        if key in expected and issue[key] != expected[key]:
            problems.append(f"{key} {issue[key]!r}, expected {expected[key]!r}")
    wanted = ", ".join(f"{k} {v}" for k, v in expected.items() if k != "issue")
    detail = f"issue #{number}: {wanted}"
    return not problems, detail + ("" if not problems else "; " + "; ".join(problems))


def _output(expected: dict[str, Any], o: Outcome) -> tuple[bool, str]:
    text = o.output or ""
    problems = [
        f"no match for {p!r}" for p in expected.get("matches", []) if not re.search(p, text)
    ]
    problems += [f"matches {p!r}" for p in expected.get("not_matches", []) if re.search(p, text)]
    detail = "final answer"
    if o.output is None:
        problems.append("no final answer")
    if problems:
        shown = text if len(text) <= 300 else text[:300] + "..."
        return False, f"{detail}: {'; '.join(problems)}; answer: {shown!r}"
    return True, detail + " matches"


_CHECKS = {
    "status": _status,
    "failure_reason": _failure_reason,
    "tool_called": _tool_called,
    "tool_not_called": _tool_not_called,
    "github": _github,
    "output": _output,
}
