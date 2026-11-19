"""The allowlist's declarative rules (ADR 0014), applied before and after a call. Pure functions:
no I/O, so each rule is tested on its own."""

import re
from typing import Any

from gateway.tools.openapi_to_mcp import Check
from gateway.tools.registry import ToolFailure


def check_values(
    checks: list[Check], values: dict[str, Any], settings: dict[str, list[str]]
) -> None:
    for check in checks:
        value = check.template.format(**values)
        allowed = settings[check.setting]
        if check.ignore_case:
            ok = value.lower() in {a.lower() for a in allowed}
        else:
            ok = value in allowed
        if not ok:
            raise ToolFailure(check.message.replace("{value}", value))


def apply_argument_rules(
    arguments: dict[str, Any], rules: dict[str, dict[str, Any]], settings: dict[str, list[str]]
) -> dict[str, Any]:
    """The arguments as sent: refused values raise, text rules (append, scope) applied in order.
    Enum, length and item limits are already in the input schema."""
    result = dict(arguments)
    for name, rule in rules.items():
        if name not in result:
            continue
        value = result[name]
        if "refuse_pattern" in rule and re.search(rule["refuse_pattern"], str(value)):
            raise ToolFailure(rule["refuse_message"])
        if "append" in rule:
            value = f"{value}{rule['append']}"
        if "scope_qualifiers" in rule:
            scope = rule["scope_qualifiers"]
            qualifiers = " ".join(f"{scope['qualifier']}:{v}" for v in settings[scope["setting"]])
            value = f"{value} {qualifiers}"
        result[name] = value
    return result


def shape_result(response: Any, result: dict[str, Any] | None) -> Any:
    """What of the response reaches the model (`result` in the allowlist)."""
    if result is None:
        return response
    fields: dict[str, str] = result["fields"]
    if "list" in result:
        items = response if result["list"] == "." else _get(response, result["list"])
        if not isinstance(items, list):
            raise ToolFailure("The API answered in an unexpected shape")
        return {"items": [_pick(item, fields) for item in items]}
    return _pick(response, fields)


def _pick(item: Any, fields: dict[str, str]) -> dict[str, Any]:
    return {out: _get(item, path) for out, path in fields.items()}


def _get(node: Any, path: str) -> Any:
    """`a.b`, or `labels[].name` (a list of that field from each element)."""
    head, sep, rest = path.partition(".")
    if head.endswith("[]"):
        values = node.get(head[:-2]) if isinstance(node, dict) else None
        if not isinstance(values, list):
            return []
        return [_get(v, rest) if sep else v for v in values]
    value = node.get(head) if isinstance(node, dict) else None
    return _get(value, rest) if sep and value is not None else value
