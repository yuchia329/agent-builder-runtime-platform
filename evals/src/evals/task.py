"""Eval tasks (evals/task.schema.json) and the specs they run (specs/<name>.yaml).

A task is checked when it is loaded: the schema, the spec it names (against
contracts/agent-spec.schema.json), every tool a check names being one of the spec's tools, and
every output pattern compiling. A broken task fails before anything runs or costs money.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parents[3]
EVALS_DIR = REPO_ROOT / "evals"
SPECS_DIR = REPO_ROOT / "specs"
CONTRACTS_DIR = REPO_ROOT / "contracts"
TASK_SCHEMA = EVALS_DIR / "task.schema.json"


class TaskError(ValueError):
    """A task or the spec it names is invalid; the message names the file and the problem."""


@dataclass(frozen=True)
class Task:
    path: Path
    name: str  # file name without .yaml
    spec_name: str
    about: str
    input: dict[str, Any]
    world: dict[str, Any]
    checks: tuple[dict[str, Any], ...]
    spec: dict[str, Any]  # the spec as published: contracts/agent-spec.schema.json


def _validator(path: Path) -> Draft202012Validator:
    schema = json.loads(path.read_text())
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _errors(validator: Draft202012Validator, data: Any) -> list[str]:
    found = sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path))
    return [f"{'/'.join(map(str, e.absolute_path)) or '(top)'}: {e.message}" for e in found]


def task_errors(data: Any) -> list[str]:
    """Schema errors of a task document (no spec lookup); empty when valid."""
    return _errors(_validator(TASK_SCHEMA), data)


def load_spec(name: str, specs_dir: Path = SPECS_DIR) -> dict[str, Any]:
    path = specs_dir / f"{name}.yaml"
    if not path.is_file():
        raise TaskError(f"Spec {name!r} not found ({path})")
    spec = yaml.safe_load(path.read_text())
    errors = _errors(_validator(CONTRACTS_DIR / "agent-spec.schema.json"), spec)
    if errors:
        raise TaskError(f"{path}: not a valid spec:\n  " + "\n  ".join(errors))
    return spec


def load_task(path: Path, specs_dir: Path = SPECS_DIR) -> Task:
    path = Path(path)
    try:
        data = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as e:
        raise TaskError(f"{path}: cannot be read: {e}") from None
    errors = task_errors(data)
    if errors:
        raise TaskError(f"{path}: not a valid task:\n  " + "\n  ".join(errors))
    spec = load_spec(data["spec"], specs_dir)
    problems = []
    for check in data["checks"]:
        ((kind, value),) = check.items()
        if kind in ("tool_called", "tool_not_called") and value["tool"] not in spec["tools"]:
            problems.append(f"{kind}: {value['tool']} is not one of the spec's tools")
        if kind == "output":
            for pattern in value.get("matches", []) + value.get("not_matches", []):
                try:
                    re.compile(pattern)
                except re.error as e:
                    problems.append(f"output: pattern {pattern!r} does not compile: {e}")
    if problems:
        raise TaskError(f"{path}:\n  " + "\n  ".join(problems))
    return Task(
        path=path,
        name=path.stem,
        spec_name=data["spec"],
        about=data["about"],
        input=data["input"],
        world=data["world"],
        checks=tuple(data["checks"]),
        spec=spec,
    )
