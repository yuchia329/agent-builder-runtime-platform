"""`uv run evals run <task.yaml>...`: runs tasks once on the real model, prints checks.
`uv run evals suite [spec...]`: runs every task of a spec several times, compares with the spec's
baseline (evals/baselines/<spec>.json) and saves full results (evals/results/, git-ignored).

The model comes from the `MODEL_*` settings in the environment (`set -a && . ../.env && set +a`),
built by the worker's own config and wiring with in-memory backends, so evals and the worker call
providers the same way (ADR 0016). Exit status: `run` 1 if any task fails; `suite` 1 on a
regression against the baseline; 2 if a task or the settings are invalid.
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

from evals import suite
from evals.runner import Evaluation, TaskResult
from evals.task import SPECS_DIR, TaskError, load_task


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
    run.add_argument("--specs-dir", type=Path, default=SPECS_DIR, help="where specs are read")
    many = commands.add_parser("suite", help="run specs' task sets and compare with baselines")
    many.add_argument("specs", nargs="*", help="spec names (default: every folder in tasks/)")
    many.add_argument("--runs", type=int, default=3, help="runs per task (default 3)")
    many.add_argument("--parallel", type=int, default=4, help="runs at once (default 4)")
    many.add_argument("--save-baseline", action="store_true", help="write the result as baseline")
    many.add_argument("--specs-dir", type=Path, default=SPECS_DIR, help="where specs are read")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s %(message)s")
    if args.command == "suite":
        return run_suites(args)

    try:
        tasks = [load_task(path, args.specs_dir) for path in args.tasks]
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


def run_suites(args: argparse.Namespace) -> int:
    names = args.specs or sorted(p.name for p in suite.TASKS_DIR.iterdir() if p.is_dir())
    if args.runs < 1 or args.parallel < 1:
        print("--runs and --parallel must be at least 1", file=sys.stderr)
        return 2
    try:
        task_sets = {name: suite.load_tasks(name, args.specs_dir) for name in names}
        model = model_from_env()
    except (TaskError, FileNotFoundError) as e:
        print(e, file=sys.stderr)
        return 2
    except ConfigError as e:
        print(f"Model settings: {e}", file=sys.stderr)
        return 2

    def progress(run: suite.RunSummary, done: int, total: int) -> None:
        mark = "." if run.passed else "F"
        print(f"\r  {done}/{total} runs {mark} {run.task} #{run.run}".ljust(90), end="", flush=True)

    regressed = False
    for name, tasks in task_sets.items():
        print(f"{name}: {len(tasks)} tasks x {args.runs} runs, {args.parallel} at once", flush=True)
        result = suite.run_suite(tasks, model, args.runs, args.parallel, progress)
        print()
        comparison = suite.compare(result, suite.read_baseline(name))
        print(suite.report(result, comparison))
        print(f"Full results: {suite.save_results(result)}")
        if args.save_baseline:
            print(f"Baseline saved: {suite.save_baseline(result)}")
        regressed |= comparison.regressed
    return 1 if regressed else 0


if __name__ == "__main__":
    sys.exit(main())
