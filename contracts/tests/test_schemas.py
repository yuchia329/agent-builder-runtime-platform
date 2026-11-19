"""Every schema is a valid JSON Schema, valid examples pass, invalid examples fail."""

import copy
import json
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"


def load(path: Path):
    text = path.read_text()
    return yaml.safe_load(text) if path.suffix in {".yaml", ".yml"} else json.loads(text)


# Schemas $ref their siblings by file name ("agent-spec.schema.json#/$defs/toolName").
REGISTRY = Registry().with_resources(
    (path.name, Resource.from_contents(load(path))) for path in ROOT.glob("*.schema.json")
)


def validator(schema_name: str) -> Draft202012Validator:
    schema = load(ROOT / f"{schema_name}.schema.json")
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, registry=REGISTRY)


SCHEMAS = ["agent-spec", "run-message", "run-token", "connector-allowlist", "mcp-tool-call"]


def example(schema_name: str, kind: str) -> Path:
    matches = sorted(EXAMPLES.glob(f"{schema_name}.{kind}.*"))
    assert len(matches) == 1, f"expected one {kind} example for {schema_name}, found {matches}"
    return matches[0]


@pytest.mark.parametrize("schema_name", SCHEMAS)
def test_valid_example_passes(schema_name):
    errors = list(validator(schema_name).iter_errors(load(example(schema_name, "valid"))))
    assert errors == [], [e.message for e in errors]


@pytest.mark.parametrize("schema_name", SCHEMAS)
def test_invalid_example_fails(schema_name):
    assert not validator(schema_name).is_valid(load(example(schema_name, "invalid")))


# Single-rule breaks of the valid spec, so each rule is checked on its own.
def _without(key):
    return lambda spec: spec.pop(key)


def _set(key, value):
    return lambda spec: spec.__setitem__(key, value)


AGENT_SPEC_BREAKS = {
    "version field present": _set("version", 3),
    "unknown field": _set("owner_email", "x@example.com"),
    "missing budget": _without("budget"),
    "missing trigger": _without("trigger"),
    "bad acts_as": _set("acts_as", "admin"),
    "empty tools": _set("tools", []),
    "duplicate tools": _set("tools", ["github.add_label", "github.add_label"]),
    "tool without connector": _set("tools", ["add_label"]),
    "uppercase name": _set("name", "Issue-Triage"),
    "trigger without dot": _set("trigger", "opened"),
    "budget missing max_usd": lambda s: s["budget"].pop("max_usd"),
    "zero max_steps": lambda s: s["budget"].__setitem__("max_steps", 0),
    "zero max_usd": lambda s: s["budget"].__setitem__("max_usd", 0),
    "model without provider": _set("model", "MiniMax-M3"),
    "model with uppercase provider": _set("model", "MiniMax/MiniMax-M3"),
    "model with hyphen in provider": _set("model", "my-host/llama3.1"),
    "model with empty id": _set("model", "minimax/"),
    "model with space": _set("model", "minimax/MiniMax M3"),
}


@pytest.mark.parametrize("break_spec", AGENT_SPEC_BREAKS.values(), ids=AGENT_SPEC_BREAKS.keys())
def test_agent_spec_rule(break_spec):
    spec = copy.deepcopy(load(example("agent-spec", "valid")))
    break_spec(spec)
    assert not validator("agent-spec").is_valid(spec)


@pytest.mark.parametrize(
    "model", ["minimax/MiniMax-M3", "openrouter/minimax/minimax-m3", "local_vllm/llama3.1:8b"]
)
def test_agent_spec_model_formats(model):
    spec = copy.deepcopy(load(example("agent-spec", "valid")))
    spec["model"] = model
    assert validator("agent-spec").is_valid(spec)


def _broken(schema_name, change):
    document = copy.deepcopy(load(example(schema_name, "valid")))
    change(document)
    return document


RUN_TOKEN_BREAKS = {
    "other audience": _set("aud", "worker"),
    "other issuer": _set("iss", "worker"),
    "missing exp": _without("exp"),
    "missing started_by": _without("started_by"),
    "no tools": _set("tools", []),
    "bad tool name": _set("tools", ["close_issue"]),
    "owner without kind": _set("owner", "tenant-demo"),
    "principal with space": _set("started_by", "api:tenant demo"),
    "credential in token": _set("github_token", "ghs_x"),
    "zero version": _set("agent_version", 0),
}


@pytest.mark.parametrize("change", RUN_TOKEN_BREAKS.values(), ids=RUN_TOKEN_BREAKS.keys())
def test_run_token_rule(change):
    assert not validator("run-token").is_valid(_broken("run-token", change))


def _tool(name, change):
    return lambda doc: change(doc["tools"][name])


ALLOWLIST_BREAKS = {
    "tool without annotations": _tool("github.add_label", lambda t: t.pop("annotations")),
    "only one annotation": _tool("github.add_label", lambda t: t["annotations"].pop("idempotentHint")),
    "unknown argument rule": _tool("github.add_label", lambda t: t["arguments"]["labels"].update(allow_any=True)),
    "refuse_pattern without message": _tool(
        "github.search_issues", lambda t: t["arguments"]["q"].pop("refuse_message")),
    "hook_arguments without hook": _tool("github.close_issue", lambda t: t.pop("hook")),
    "check without setting": _tool("github.close_issue", lambda t: t["checks"][0].pop("in_setting")),
    "lowercase setting": _tool("github.close_issue", lambda t: t["checks"][0].update(in_setting="repos")),
    "tool name without connector": lambda doc: doc["tools"].update(add_label=doc["tools"].pop("github.add_label")),
    "unknown credential kind": lambda doc: doc["credentials"].update({"team-bot": "personal_token"}),
    "http base url": _set("base_url", "http://api.github.com"),
    "hook name with dash": _tool("github.close_issue", lambda t: t.update(hook="close-checks")),
    "authorization header": lambda doc: doc["headers"].update(Authorization="Bearer x"),
    "lowercase authorization header": lambda doc: doc["headers"].update(authorization="x"),
    "non-string header": lambda doc: doc["headers"].update({"X-Number": 1}),
}


@pytest.mark.parametrize("change", ALLOWLIST_BREAKS.values(), ids=ALLOWLIST_BREAKS.keys())
def test_connector_allowlist_rule(change):
    assert not validator("connector-allowlist").is_valid(_broken("connector-allowlist", change))


MCP_CALL_BREAKS = {
    "no _meta": _without("_meta"),
    "no step": lambda c: c["_meta"].pop("agentplatform/step"),
    "step 0": lambda c: c["_meta"].update({"agentplatform/step": 0}),
    "short idempotency key": lambda c: c["_meta"].update({"agentplatform/idempotency_key": "abc"}),
    "bad tool name": _set("name", "close_issue"),
    "arguments not an object": _set("arguments", ["owner"]),
}


@pytest.mark.parametrize("change", MCP_CALL_BREAKS.values(), ids=MCP_CALL_BREAKS.keys())
def test_mcp_tool_call_rule(change):
    assert not validator("mcp-tool-call").is_valid(_broken("mcp-tool-call", change))
