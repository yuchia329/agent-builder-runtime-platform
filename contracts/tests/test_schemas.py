"""Every schema is a valid JSON Schema, valid examples pass, invalid examples fail."""

import copy
import json
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"


def load(path: Path):
    text = path.read_text()
    return yaml.safe_load(text) if path.suffix in {".yaml", ".yml"} else json.loads(text)


def validator(schema_name: str) -> Draft202012Validator:
    schema = load(ROOT / f"{schema_name}.schema.json")
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


SCHEMAS = ["agent-spec", "run-message"]


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
}


@pytest.mark.parametrize("break_spec", AGENT_SPEC_BREAKS.values(), ids=AGENT_SPEC_BREAKS.keys())
def test_agent_spec_rule(break_spec):
    spec = copy.deepcopy(load(example("agent-spec", "valid")))
    break_spec(spec)
    assert not validator("agent-spec").is_valid(spec)
