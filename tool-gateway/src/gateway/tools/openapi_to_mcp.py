"""The generator (ADR 0014): a connector folder (openapi.yaml + allowlist.yaml) becomes MCP tools.

Each allowed tool exposes only its listed arguments, under the API's own parameter names, with an
input schema taken from the operation (refs resolved, OpenAPI-only keywords dropped) and narrowed
by the allowlist's rules. Everything the contract cannot express is checked here at load, so a bad
connector fails startup, never a call.
"""

import copy
import re
import string
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from gateway.contracts import Contracts

METHODS = ("get", "put", "post", "delete", "patch")
OPENAPI_ONLY = {"nullable", "example", "examples", "externalDocs", "xml", "discriminator"}


class ConnectorError(ValueError):
    """A connector folder that cannot be loaded. Fails startup."""


@dataclass(frozen=True)
class Operation:
    operation_id: str
    method: str
    path: str
    summary: str
    parameters: dict[str, dict[str, Any]]  # name -> {"in", "required", "schema"} (path, query)
    body: dict[str, Any] | None  # resolved object schema of a JSON body, if any


@dataclass(frozen=True)
class Check:
    template: str
    setting: str
    ignore_case: bool
    message: str


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]
    operation: Operation
    argument_rules: dict[str, dict[str, Any]]
    fixed: dict[str, Any]
    checks: list[Check]
    result: dict[str, Any] | None
    read_only: bool
    idempotent: bool
    destructive: bool
    hook: Callable | None = None
    hook_arguments: frozenset[str] = frozenset()


@dataclass(frozen=True)
class ConnectorDefinition:
    name: str
    base_url: str
    headers: dict[str, str]
    credentials: dict[str, str]
    operations: dict[str, Operation]  # every operation the connector may call
    hook_operations: frozenset[str]
    tools: dict[str, ToolDefinition]
    settings: dict[str, list[str]] = field(default_factory=dict)  # resolved setting lists


def load_connector(
    folder: Path,
    contracts: Contracts,
    env: Mapping[str, str],
    hooks: Mapping[str, Callable] | None = None,
) -> ConnectorDefinition:
    allowlist = _yaml(folder / "allowlist.yaml")
    errors = contracts.errors("connector-allowlist", allowlist)
    if errors:
        raise ConnectorError(f"{folder.name}/allowlist.yaml: " + "; ".join(errors[:5]))
    spec = _yaml(folder / "openapi.yaml")
    all_operations = _operations(spec)
    name = allowlist["connector"]
    hooks = hooks or {}

    hook_operations = frozenset(allowlist.get("hook_operations", []))
    missing = sorted(hook_operations - set(all_operations))
    if missing:
        raise ConnectorError(f"{name}: hook_operations not in openapi.yaml: {', '.join(missing)}")

    settings: dict[str, list[str]] = {}
    tools: dict[str, ToolDefinition] = {}
    for tool_name, entry in allowlist["tools"].items():
        where = f"{name}: {tool_name}"
        if not tool_name.startswith(f"{name}."):
            raise ConnectorError(f"{where}: tool names must start with '{name}.'")
        operation = all_operations.get(entry["operation"])
        if operation is None:
            raise ConnectorError(f"{where}: operation {entry['operation']!r} not in openapi.yaml")
        tool = _tool(tool_name, entry, operation, hooks, where)
        for setting in _settings_used(entry):
            settings[setting] = _setting(env, setting, where)
        tools[tool_name] = tool

    kept = {op: all_operations[op] for op in {t.operation.operation_id for t in tools.values()}}
    kept |= {op: all_operations[op] for op in hook_operations}
    return ConnectorDefinition(
        name=name,
        base_url=allowlist["base_url"].rstrip("/"),
        headers=dict(allowlist.get("headers", {})),
        credentials=dict(allowlist["credentials"]),
        operations=kept,
        hook_operations=hook_operations,
        tools=tools,
        settings=settings,
    )


def _tool(
    name: str,
    entry: dict[str, Any],
    operation: Operation,
    hooks: Mapping[str, Callable],
    where: str,
) -> ToolDefinition:
    arguments: dict[str, dict[str, Any]] = entry["arguments"]
    fixed: dict[str, Any] = entry.get("fixed", {})
    hook_arguments: dict[str, dict[str, Any]] = entry.get("hook_arguments", {})
    body_props = (operation.body or {}).get("properties", {})
    known = set(operation.parameters) | set(body_props)

    for arg in [*arguments, *fixed]:
        if arg not in known:
            raise ConnectorError(f"{where}: {arg!r} is not a parameter of {operation.operation_id}")
    both = set(arguments) & set(fixed)
    if both:
        raise ConnectorError(f"{where}: {', '.join(sorted(both))} both listed and fixed")
    clash = set(hook_arguments) & known
    if clash:
        raise ConnectorError(
            f"{where}: hook argument {', '.join(sorted(clash))} is an API parameter"
        )
    for check in entry.get("checks", []):
        for _, field_name, _, _ in string.Formatter().parse(check["value"]):
            if field_name is not None and field_name not in arguments and field_name not in fixed:
                raise ConnectorError(f"{where}: check uses unknown argument {{{field_name}}}")
    for arg, rules in arguments.items():
        if "refuse_pattern" in rules:
            try:
                re.compile(rules["refuse_pattern"])
            except re.error as e:
                raise ConnectorError(f"{where}: {arg}: bad refuse_pattern ({e})") from e
    hook = None
    if "hook" in entry:
        hook = hooks.get(entry["hook"])
        if hook is None:
            raise ConnectorError(f"{where}: hook {entry['hook']!r} is not defined")

    properties: dict[str, Any] = {}
    required: list[str] = []
    body_required = set((operation.body or {}).get("required", []))
    for arg, rules in arguments.items():
        if arg in operation.parameters:
            param = operation.parameters[arg]
            schema = copy.deepcopy(param["schema"])
            if param.get("description") and "description" not in schema:
                schema["description"] = param["description"]
            is_required = param["required"]
        else:
            schema = copy.deepcopy(body_props[arg])
            is_required = arg in body_required
        properties[arg] = _narrow(schema, rules, f"{where}: {arg}")
        if is_required:
            required.append(arg)
    for arg, schema in hook_arguments.items():
        properties[arg] = copy.deepcopy(schema)

    annotations = entry["annotations"]
    return ToolDefinition(
        name=name,
        description=entry.get("description") or operation.summary,
        input_schema={
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
        operation=operation,
        argument_rules=arguments,
        fixed=fixed,
        checks=[
            Check(c["value"], c["in_setting"], c.get("ignore_case", False), c["message"])
            for c in entry.get("checks", [])
        ],
        result=entry.get("result"),
        read_only=annotations["readOnlyHint"],
        idempotent=annotations["idempotentHint"],
        destructive=annotations.get("destructiveHint", False),
        hook=hook,
        hook_arguments=frozenset(hook_arguments),
    )


def _narrow(schema: dict[str, Any], rules: dict[str, Any], where: str) -> dict[str, Any]:
    """The operation's schema for one argument, limited by the allowlist (never widened)."""
    if "enum" in rules:
        allowed = schema.get("enum")
        extra = [v for v in rules["enum"] if allowed is not None and v not in allowed]
        if extra:
            raise ConnectorError(f"{where}: enum values the API does not accept: {extra}")
        schema["enum"] = list(rules["enum"])
    if "max_length" in rules:
        schema["maxLength"] = min(rules["max_length"], schema.get("maxLength", rules["max_length"]))
    if "max_items" in rules:
        schema["maxItems"] = min(rules["max_items"], schema.get("maxItems", rules["max_items"]))
    return schema


def _settings_used(entry: dict[str, Any]) -> set[str]:
    used = {c["in_setting"] for c in entry.get("checks", [])}
    for rules in entry["arguments"].values():
        if "scope_qualifiers" in rules:
            used.add(rules["scope_qualifiers"]["setting"])
    return used


def _setting(env: Mapping[str, str], name: str, where: str) -> list[str]:
    values = [v.strip() for v in env.get(name, "").split(",") if v.strip()]
    if not values:
        raise ConnectorError(f"{where}: setting {name} is not set (comma-separated list)")
    return values


def _operations(spec: dict[str, Any]) -> dict[str, Operation]:
    operations: dict[str, Operation] = {}
    for path, item in spec.get("paths", {}).items():
        shared = item.get("parameters", [])
        for method in METHODS:
            op = item.get(method)
            if not op:
                continue
            params: dict[str, dict[str, Any]] = {}
            for raw in [*shared, *op.get("parameters", [])]:
                param = _resolve(spec, raw)
                if param["in"] not in ("path", "query"):
                    continue
                params[param["name"]] = {
                    "in": param["in"],
                    "required": bool(param.get("required")) or param["in"] == "path",
                    "schema": _clean(spec, param.get("schema", {})),
                    "description": param.get("description", ""),
                }
            operations[op["operationId"]] = Operation(
                operation_id=op["operationId"],
                method=method.upper(),
                path=path,
                summary=op.get("summary", op["operationId"]),
                parameters=params,
                body=_json_body(spec, op),
            )
    return operations


def _json_body(spec: dict[str, Any], op: dict[str, Any]) -> dict[str, Any] | None:
    content = _resolve(spec, op.get("requestBody", {})).get("content", {})
    schema = content.get("application/json", {}).get("schema")
    if schema is None:
        return None
    schema = _clean(spec, schema)
    if "properties" in schema:
        return schema
    # A body offered in several shapes (GitHub's add-labels): use the object variant.
    for variant in schema.get("oneOf", []) + schema.get("anyOf", []):
        if (
            isinstance(variant, dict)
            and variant.get("type") == "object"
            and "properties" in variant
        ):
            return variant
    return None


def _resolve(spec: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    while isinstance(node, dict) and "$ref" in node:
        node = _lookup(spec, node["$ref"])
    return node


def _lookup(spec: dict[str, Any], ref: str) -> Any:
    if not ref.startswith("#/"):
        raise ConnectorError(f"Only local refs are supported: {ref}")
    node: Any = spec
    for part in ref[2:].split("/"):
        node = node[part.replace("~1", "/").replace("~0", "~")]
    return node


def _clean(spec: dict[str, Any], schema: Any, depth: int = 0) -> Any:
    """Refs inlined and OpenAPI-only keywords dropped, so the model gets plain JSON Schema."""
    if depth > 20:
        raise ConnectorError("Schema nesting too deep (recursive $ref?)")
    if isinstance(schema, list):
        return [_clean(spec, item, depth + 1) for item in schema]
    if not isinstance(schema, dict):
        return schema
    if "$ref" in schema:
        return _clean(spec, _lookup(spec, schema["$ref"]), depth + 1)
    cleaned = {}
    for key, value in schema.items():
        if key in ("properties", "patternProperties", "$defs"):  # names here are data, not keywords
            cleaned[key] = {k: _clean(spec, v, depth + 1) for k, v in value.items()}
        elif key not in OPENAPI_ONLY:
            cleaned[key] = _clean(spec, value, depth + 1)
    return cleaned


def _yaml(path: Path) -> Any:
    if not path.is_file():
        raise ConnectorError(f"Missing {path}")
    with path.open() as f:
        return yaml.safe_load(f)
