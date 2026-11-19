"""`uv run evals run <task.yaml>...`: runs tasks once on the real model, prints checks.
`uv run evals suite [spec...]`: runs every task of a spec several times, compares with the spec's
baseline (evals/baselines/<spec>.json) and saves full results (evals/results/, git-ignored).
`uv run evals labels <spec>`: writes evals/labels/<spec>.yaml from saved results, for hand labels.
`uv run evals judge-check <spec>`: grades the labeled answers, reports agreement, saves
evals/judges/<spec>.json (the judge counts in suites once trusted).

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

from evals import judge as judging
from evals import labels, suite
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
    many.add_argument("--no-judge", action="store_true", help="skip the judge (saves its cost)")
    sample = commands.add_parser("labels", help="write a labels file to fill in by hand")
    sample.add_argument("spec")
    sample.add_argument("--force", action="store_true", help="replace an existing labels file")
    check = commands.add_parser("judge-check", help="compare the judge with the hand labels")
    check.add_argument("spec")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s %(message)s")
    if args.command == "suite":
        return run_suites(args)
    if args.command == "labels":
        return write_labels(args)
    if args.command == "judge-check":
        return judge_check(args)

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
        rubric = None if args.no_judge else judging.read_rubric(name)
        judge = judging.Judge(model, rubric) if rubric else None
        counted = judge is not None and judging.trusted(name, rubric)
        if judge:
            print(f"  judge {judge.model_id}: {'counted' if counted else 'reported only'}")
        result = suite.run_suite(tasks, model, args.runs, args.parallel, progress, judge, counted)
        print()
        comparison = suite.compare(result, suite.read_baseline(name))
        print(suite.report(result, comparison))
        print(f"Full results: {suite.save_results(result)}")
        if args.save_baseline:
            print(f"Baseline saved: {suite.save_baseline(result)}")
        regressed |= comparison.regressed
    return 1 if regressed else 0


def write_labels(args: argparse.Namespace) -> int:
    path = labels.LABELS_DIR / f"{args.spec}.yaml"
    if path.exists() and not args.force:
        print(
            f"{path} exists; it may hold your labels. Use --force to replace it.", file=sys.stderr
        )
        return 2
    try:
        items = labels.sample(args.spec)
    except (FileNotFoundError, TaskError) as e:
        print(e, file=sys.stderr)
        return 2
    print(f"Wrote {len(items)} items to {labels.write(args.spec, items)}")
    return 0


def judge_check(args: argparse.Namespace) -> int:
    rubric = judging.read_rubric(args.spec)
    if rubric is None:
        print(f"No rubric {judging.rubric_path(args.spec)}", file=sys.stderr)
        return 2
    try:
        items = [i for i in labels.read(args.spec) if i.get("label") in ("good", "bad")]
        model = model_from_env()
    except (FileNotFoundError, ValueError) as e:
        print(e, file=sys.stderr)
        return 2
    except ConfigError as e:
        print(f"Model settings: {e}", file=sys.stderr)
        return 2
    if not items:
        print("No labeled items yet: set label to good or bad.", file=sys.stderr)
        return 2
    judge = judging.Judge(model, rubric)
    pairs = []
    for item, verdict in zip(items, judging.grade_labeled(judge, items), strict=True):
        pairs.append((item["label"], verdict))
        if verdict.verdict != item["label"]:
            print(
                f"  #{item['id']} {item['task']}: label {item['label']}, judge {verdict.verdict}: "
                f"{verdict.why}"
            )
    result = judging.calibration(args.spec, rubric, judge.model_id, pairs)
    print(
        f"Agreement {result['agreement']:.0%} on {result['labeled']} labels; bad caught "
        f"{result['bad_caught']}/{result['bad_labels']}; unreadable {result['errors']}; "
        f"cost ${sum(v.cost_usd for _, v in pairs):.4f}"
    )
    print(
        f"Trusted: {'yes' if result['trusted'] else 'no'} (needs {judging.MIN_AGREEMENT:.0%} "
        f"agreement, {judging.MIN_BAD}+ bad labels, {judging.MIN_BAD_CAUGHT:.0%} of them caught)"
    )
    print(f"Saved {judging.save_calibration(result)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
