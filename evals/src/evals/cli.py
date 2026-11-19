"""`uv run evals run <task.yaml>...`: runs tasks on the spec's real model and prints each check.

The model comes from the `MODEL_*` settings in the environment (`set -a && . ../.env && set +a`),
built by the worker's own config and wiring with in-memory backends, so evals and the worker call
providers the same way (ADR 0016). Exit status 1 if any task fails, 2 if a task is invalid.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Mapping
from pathlib import Path

from worker.config import Config, ConfigError
from worker.ports.model import Model
from worker.wiring import build

from evals.runner import Evaluation, TaskResult
from evals.task import TaskError, load_task


def model_from_env(env: Mapping[str, str] = os.environ) -> Model:
    """The worker's model router (providers from MODEL_PROVIDERS and MODEL_<NAME>_*)."""
    settings = dict(env) | {
        "QUEUE_BACKEND": "memory",
        "STATE_BACKEND": "memory",
        "TOOLS_PROVIDER": "fake",  # tools are the gateway's; only the model is taken
    }
    return build(Config.from_env(settings)).deps.model


def report(result: TaskResult) -> str:
    o = result.outcome
    head = "PASS" if result.passed else "FAIL"
    lines = [
        f"{head} {result.task.spec_name}/{result.task.name}: {result.task.about}",
        f"     run {result.run_id}: {o.status}, {o.steps} steps, ${o.cost_usd:.4f}, "
        f"{len(o.calls)} tool calls, {len(o.approvals)} approved automatically",
    ]
    for check in result.checks:
        lines.append(f"     {'ok  ' if check.passed else 'FAIL'} {check.kind}: {check.detail}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="evals", description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="run tasks and print their checks")
    run.add_argument("tasks", nargs="+", type=Path, help="task files (evals/tasks/<spec>/*.yaml)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s %(message)s")

    try:
        tasks = [load_task(path) for path in args.tasks]
    except TaskError as e:
        print(e, file=sys.stderr)
        return 2
    try:
        model = model_from_env()
    except ConfigError as e:
        print(f"Model settings: {e}", file=sys.stderr)
        return 2

    failed = 0
    with Evaluation() as evaluation:
        for task in tasks:
            result = evaluation.run(task, model)
            failed += not result.passed
            print(report(result), flush=True)
    print(f"\n{len(tasks) - failed}/{len(tasks)} tasks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
