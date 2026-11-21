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


APPROVED = {
    "step": 4,
    "tool": "github.close_issue",
    "arguments": {"repo": "acme/payments", "issue_number": 42, "reason": "duplicate"},
    "decision": "approved",
    "decided_by": "U024BE7LH",
    "decided_at": "2026-11-17T10:00:00Z",
}


@pytest.mark.parametrize("decision", ["approved", "rejected"])
def test_run_with_a_decided_approval_passes(decision):
    run = copy.deepcopy(load(EXAMPLES / "internal-run.valid.json"))
    run["approval"] = APPROVED | {"decision": decision}
    errors = list(component("InternalRun").iter_errors(run))
    assert errors == [], [e.message for e in errors]
    assert component("RunSummary").is_valid({k: v for k, v in run.items() if k not in ("spec", "input")})


def _without(key):
    return {k: v for k, v in APPROVED.items() if k != key}


BAD_APPROVALS = {
    "unknown decision": APPROVED | {"decision": "maybe"},
    "missing decided_by": _without("decided_by"),
    "empty decided_by": APPROVED | {"decided_by": ""},
    "missing arguments": _without("arguments"),
    "bad tool name": APPROVED | {"tool": "close issue"},
    "step zero": APPROVED | {"step": 0},
    "unknown field": APPROVED | {"comment": "ok"},
}


@pytest.mark.parametrize("approval", BAD_APPROVALS.values(), ids=BAD_APPROVALS.keys())
def test_run_with_a_bad_approval_fails(approval):
    run = copy.deepcopy(load(EXAMPLES / "internal-run.valid.json"))
    run["approval"] = approval
    assert not component("InternalRun").is_valid(run)


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
    "approval_expired is a known reason": {
        "status": "FAILED", "failure": {"reason": "approval_expired", "message": "72 h"}},
    "rate_limited is a known reason": {
        "status": "FAILED", "failure": {"reason": "rate_limited", "message": "15 min set aside"}},
    "tenant_budget is a known reason": {
        "status": "FAILED", "failure": {"reason": "tenant_budget", "message": "daily cap"}},
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
    "workers never send a decision": {"status": "RUNNING", "approval": APPROVED},
    "missing status": {"steps": 1},
}


@pytest.mark.parametrize("update", OK_UPDATES.values(), ids=OK_UPDATES.keys())
def test_run_update_accepts(update):
    errors = list(component("RunUpdate").iter_errors(update))
    assert errors == [], [e.message for e in errors]


@pytest.mark.parametrize("update", BAD_UPDATES.values(), ids=BAD_UPDATES.keys())
def test_run_update_rejects(update):
    assert not component("RunUpdate").is_valid(update)


def test_run_defer_example_passes():
    errors = list(component("RunDefer").iter_errors(load(EXAMPLES / "internal-run-defer.valid.json")))
    assert errors == [], [e.message for e in errors]


def test_run_defer_invalid_example_fails():
    assert not component("RunDefer").is_valid(load(EXAMPLES / "internal-run-defer.invalid.json"))


RUN_DEFER_BREAKS = {
    "missing reason": lambda d: d.pop("reason"),
    "unknown reason": lambda d: d.__setitem__("reason", "busy"),
    "missing delay": lambda d: d.pop("retry_after_seconds"),
    "zero delay": lambda d: d.__setitem__("retry_after_seconds", 0),
    "delay over SQS maximum": lambda d: d.__setitem__("retry_after_seconds", 901),
    "fractional delay": lambda d: d.__setitem__("retry_after_seconds", 1.5),
    "unknown field": lambda d: d.__setitem__("attempt", 2),
}


@pytest.mark.parametrize("break_defer", RUN_DEFER_BREAKS.values(), ids=RUN_DEFER_BREAKS.keys())
def test_run_defer_rule(break_defer):
    defer = copy.deepcopy(load(EXAMPLES / "internal-run-defer.valid.json"))
    break_defer(defer)
    assert not component("RunDefer").is_valid(defer)


def test_deferred_problem_example_passes():
    errors = list(
        component("DeferredProblem").iter_errors(load(EXAMPLES / "deferred-problem.valid.json"))
    )
    assert errors == [], [e.message for e in errors]


DEFERRED_PROBLEM_BREAKS = {
    "other problem type": lambda d: d.__setitem__("type", "about:blank"),
    "status not 429": lambda d: d.__setitem__("status", 409),
    "unknown reason": lambda d: d.__setitem__("reason", "busy"),
    "missing delay": lambda d: d.pop("retry_after_seconds"),
    "delay over SQS maximum": lambda d: d.__setitem__("retry_after_seconds", 1000),
    "missing reason": lambda d: d.pop("reason"),
}


@pytest.mark.parametrize(
    "break_problem", DEFERRED_PROBLEM_BREAKS.values(), ids=DEFERRED_PROBLEM_BREAKS.keys()
)
def test_deferred_problem_rule(break_problem):
    problem = copy.deepcopy(load(EXAMPLES / "deferred-problem.valid.json"))
    break_problem(problem)
    assert not component("DeferredProblem").is_valid(problem)


def test_start_and_defer_document_their_responses():
    paths = api_doc()["paths"]
    patch = paths["/internal/runs/{run_id}"]["patch"]["responses"]
    assert patch["429"] == {"$ref": "#/components/responses/Deferred"}
    defer = paths["/internal/runs/{run_id}/defer"]["post"]
    assert defer["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/RunDefer"
    }
    assert set(defer["responses"]) == {"200", "400", "403", "404", "409"}
