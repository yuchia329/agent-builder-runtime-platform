"""Test helpers that read the shared contracts/ files, so tests check the worker against them."""

import json
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

CONTRACTS = Path(__file__).resolve().parents[2] / "contracts"
API_FILE = CONTRACTS / "internal-api.openapi.yaml"


def _registry() -> Registry:
    return Registry().with_resources(
        [
            (
                API_FILE.as_uri(),
                Resource.from_contents(
                    yaml.safe_load(API_FILE.read_text()), default_specification=DRAFT202012
                ),
            ),
            (
                (CONTRACTS / "agent-spec.schema.json").as_uri(),
                Resource.from_contents(
                    json.loads((CONTRACTS / "agent-spec.schema.json").read_text())
                ),
            ),
        ]
    )


def api_schema(name: str) -> Draft202012Validator:
    """Validator for components/schemas/<name> in internal-api.openapi.yaml."""
    return Draft202012Validator(
        {"$ref": f"{API_FILE.as_uri()}#/components/schemas/{name}"}, registry=_registry()
    )


def contract_errors(name: str, instance: Any) -> list[str]:
    return [e.message for e in api_schema(name).iter_errors(instance)]


def example_run() -> dict[str, Any]:
    """contracts/examples/internal-run.valid.json: a QUEUED issue-triage run."""
    return json.loads((CONTRACTS / "examples" / "internal-run.valid.json").read_text())
