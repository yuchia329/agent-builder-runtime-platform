"""internal-api.openapi.yaml is a valid OpenAPI 3.1 document and its schemas accept/reject examples."""

import copy
import json
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator
from openapi_spec_validator import validate as validate_openapi
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"
API_FILE = ROOT / "internal-api.openapi.yaml"
API_URI = API_FILE.as_uri()


def load(path: Path):
    text = path.read_text()
    return yaml.safe_load(text) if path.suffix in {".yaml", ".yml"} else json.loads(text)


def api_doc() -> dict:
    return load(API_FILE)


def component(name: str) -> Draft202012Validator:
    """Validator for components/schemas/<name>, with refs into the doc and to sibling schema files."""
    registry = Registry().with_resources([
        (API_URI, Resource.from_contents(api_doc(), default_specification=DRAFT202012)),
        ((ROOT / "agent-spec.schema.json").as_uri(),
         Resource.from_contents(load(ROOT / "agent-spec.schema.json"))),
    ])
    return Draft202012Validator({"$ref": f"{API_URI}#/components/schemas/{name}"}, registry=registry)


def test_document_is_valid_openapi():
    validate_openapi(api_doc(), base_uri=API_URI)


def test_internal_run_example_passes():
    errors = list(component("InternalRun").iter_errors(load(EXAMPLES / "internal-run.valid.json")))
    assert errors == [], [e.message for e in errors]


def test_run_summary_is_internal_run_without_spec_and_input():
    run = load(EXAMPLES / "internal-run.valid.json")
    del run["spec"], run["input"]
    assert component("RunSummary").is_valid(run)


def test_run_update_example_passes():
    errors = list(component("RunUpdate").iter_errors(load(EXAMPLES / "internal-run-update.valid.json")))
    assert errors == [], [e.message for e in errors]


def test_run_update_invalid_example_fails():
    assert not component("RunUpdate").is_valid(load(EXAMPLES / "internal-run-update.invalid.json"))


INTERNAL_RUN_BREAKS = {
    "missing spec": lambda r: r.pop("spec"),
    "missing input": lambda r: r.pop("input"),
    "spec breaks agent-spec schema": lambda r: r["spec"].pop("budget"),
    "unknown field": lambda r: r.__setitem__("worker_id", "w-1"),
    "bad status": lambda r: r.__setitem__("status", "DONE"),
    "version zero": lambda r: r.__setitem__("agent_version", 0),
}


@pytest.mark.parametrize("break_run", INTERNAL_RUN_BREAKS.values(), ids=INTERNAL_RUN_BREAKS.keys())
def test_internal_run_rule(break_run):
    run = copy.deepcopy(load(EXAMPLES / "internal-run.valid.json"))
    break_run(run)
    assert not component("InternalRun").is_valid(run)


OK_UPDATES = {
    "running with progress": {"status": "RUNNING", "steps": 2, "cost_usd": 0.001},
    "succeeded with output": {"status": "SUCCEEDED", "steps": 9, "output": "Labeled as bug."},
    "failed with reason": {"status": "FAILED", "failure": {"reason": "budget_steps", "message": "20 steps"}},
}

BAD_UPDATES = {
    "queued is not a worker status": {"status": "QUEUED"},
    "waiting without pending call": {"status": "WAITING_APPROVAL"},
    "pending call while running": {"status": "RUNNING", "pending_approval": {
        "step": 1, "tool": "github.close_issue", "arguments": {}}},
    "failure while succeeded": {"status": "SUCCEEDED", "failure": {"reason": "error", "message": "x"}},
    "output while running": {"status": "RUNNING", "output": "partial"},
    "unknown failure reason": {"status": "FAILED", "failure": {"reason": "oops", "message": "x"}},
    "negative cost": {"status": "RUNNING", "cost_usd": -1},
    "bad trace id": {"status": "RUNNING", "trace_id": "not-hex"},
    "unknown field": {"status": "RUNNING", "worker": "w-1"},
    "missing status": {"steps": 1},
}


@pytest.mark.parametrize("update", OK_UPDATES.values(), ids=OK_UPDATES.keys())
def test_run_update_accepts(update):
    errors = list(component("RunUpdate").iter_errors(update))
    assert errors == [], [e.message for e in errors]


@pytest.mark.parametrize("update", BAD_UPDATES.values(), ids=BAD_UPDATES.keys())
def test_run_update_rejects(update):
    assert not component("RunUpdate").is_valid(update)
