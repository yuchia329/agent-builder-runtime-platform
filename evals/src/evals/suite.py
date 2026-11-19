"""A spec's whole task set, run several times, summarized and compared with its baseline.

Each task runs `runs` times (default 3) and passes when at least two thirds of its runs pass.
`parallel` workers each own an `Evaluation` (simulator and gateway), so runs never share state.

Comparison with `evals/baselines/<spec>.json` (only tasks in both count):

- a task regresses when its pass fraction drops by at least 2/3 (3/3 to 1/3 or 0/3, 2/3 to 0/3);
  smaller moves are listed as changed, because a task that passes about half the time flips
  between suites by chance;
- the suite regresses when the run pass rate drops by more than 5 points.
"""

from __future__ import annotations

import hashlib
import json
import queue
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from worker.ports.model import Model

from evals.runner import Evaluation
from evals.task import EVALS_DIR, SPECS_DIR, Task, load_task

BASELINES_DIR = EVALS_DIR / "baselines"
RESULTS_DIR = EVALS_DIR / "results"
TASKS_DIR = EVALS_DIR / "tasks"
TASK_DROP = 2 / 3 - 1e-9  # pass-fraction drop that makes a task a regression
RATE_DROP = 0.05  # run pass-rate drop that makes the suite a regression


@dataclass(frozen=True)
class RunSummary:
    task: str
    run: int
    passed: bool
    status: str
    steps: int
    cost_usd: float
    checks: list[dict[str, Any]]  # kind, passed, detail
    calls: list[dict[str, Any]]  # tool, arguments, error
    output: str | None
    error: str | None = None  # the run itself broke (not a check failure)


@dataclass
class SuiteResult:
    spec: str
    model: str
    spec_sha256: str
    runs_per_task: int
    runs: list[RunSummary] = field(default_factory=list)

    def task_passes(self) -> dict[str, int]:
        passes = Counter({r.task: 0 for r in self.runs})
        passes.update(r.task for r in self.runs if r.passed)
        return dict(sorted(passes.items()))

    def task_runs(self) -> dict[str, int]:
        return dict(sorted(Counter(r.task for r in self.runs).items()))

    @property
    def pass_rate(self) -> float:
        return sum(r.passed for r in self.runs) / len(self.runs) if self.runs else 0.0

    @property
    def cost_usd(self) -> float:
        return sum(r.cost_usd for r in self.runs)

    def check_kinds(self) -> dict[str, tuple[int, int]]:
        passed: Counter[str] = Counter()
        total: Counter[str] = Counter()
        for r in self.runs:
            for c in r.checks:
                total[c["kind"]] += 1
                passed[c["kind"]] += c["passed"]
        return {k: (passed[k], total[k]) for k in sorted(total)}

    def baseline(self) -> dict[str, Any]:
        runs = self.task_runs()
        return {
            "spec": self.spec,
            "model": self.model,
            "spec_sha256": self.spec_sha256,
            "date": datetime.now(UTC).strftime("%Y-%m-%d"),
            "runs_per_task": self.runs_per_task,
            "pass_rate": round(self.pass_rate, 4),
            "cost_usd": round(self.cost_usd, 4),
            "tasks": {t: {"passes": p, "runs": runs[t]} for t, p in self.task_passes().items()},
        }


def spec_hash(spec: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()


def load_tasks(spec_name: str, specs_dir: Path = SPECS_DIR) -> list[Task]:
    folder = TASKS_DIR / spec_name
    paths = sorted(folder.glob("*.yaml"))
    if not paths:
        raise FileNotFoundError(f"No tasks in {folder}")
    return [load_task(path, specs_dir) for path in paths]


def run_suite(
    tasks: list[Task], model: Model, runs: int = 3, parallel: int = 4, progress=None
) -> SuiteResult:
    spec = tasks[0].spec
    result = SuiteResult(spec["name"], spec["model"], spec_hash(spec), runs)
    jobs = [(task, n) for n in range(1, runs + 1) for task in tasks]
    workers = max(1, min(parallel, len(jobs)))
    free: queue.Queue[Evaluation] = queue.Queue()
    lock = threading.Lock()
    with ExitStack() as stack:
        for _ in range(workers):
            free.put(stack.enter_context(Evaluation()))

        def one(job: tuple[Task, int]) -> RunSummary:
            task, n = job
            evaluation = free.get()
            try:
                summary = _run_once(evaluation, task, n, model)
            finally:
                free.put(evaluation)
            with lock:
                result.runs.append(summary)
                if progress:
                    progress(summary, len(result.runs), len(jobs))
            return summary

        with ThreadPoolExecutor(workers) as pool:
            list(pool.map(one, jobs))
    result.runs.sort(key=lambda r: (r.task, r.run))
    return result


def _run_once(evaluation: Evaluation, task: Task, n: int, model: Model) -> RunSummary:
    try:
        r = evaluation.run(task, model)
    except Exception as e:  # noqa: BLE001 - a broken run fails its task, the suite goes on
        return RunSummary(task.name, n, False, "ERROR", 0, 0.0, [], [], None, repr(e)[:500])
    o = r.outcome
    return RunSummary(
        task=task.name,
        run=n,
        passed=r.passed,
        status=o.status,
        steps=o.steps,
        cost_usd=o.cost_usd,
        checks=[{"kind": c.kind, "passed": c.passed, "detail": c.detail} for c in r.checks],
        calls=[{"tool": c.tool, "arguments": c.arguments, "error": c.error} for c in o.calls],
        output=o.output,
    )


@dataclass(frozen=True)
class Comparison:
    regressions: list[str]  # task: base -> now
    changed: list[str]
    new_tasks: list[str]
    missing_tasks: list[str]
    base_rate: float | None
    rate: float
    notes: list[str]  # model or spec differs

    @property
    def regressed(self) -> bool:
        return bool(self.regressions) or (
            self.base_rate is not None and self.base_rate - self.rate > RATE_DROP
        )


def compare(result: SuiteResult, baseline: dict[str, Any] | None) -> Comparison:
    if baseline is None:
        return Comparison([], [], list(result.task_passes()), [], None, result.pass_rate, [])
    passes, runs = result.task_passes(), result.task_runs()
    base = baseline["tasks"]
    regressions, changed = [], []
    for task in sorted(set(passes) & set(base)):
        before = base[task]["passes"] / base[task]["runs"]
        now = passes[task] / runs[task]
        line = f"{task}: {base[task]['passes']}/{base[task]['runs']} -> {passes[task]}/{runs[task]}"
        if before - now >= TASK_DROP:
            regressions.append(line)
        elif _majority(before) != _majority(now):
            changed.append(line)
    common = set(passes) & set(base)
    base_runs = sum(base[t]["runs"] for t in common)
    base_rate = sum(base[t]["passes"] for t in common) / base_runs if base_runs else None
    rate_runs = sum(runs[t] for t in common)
    rate = sum(passes[t] for t in common) / rate_runs if rate_runs else result.pass_rate
    notes = []
    if baseline.get("model") != result.model:
        notes.append(f"model differs: baseline {baseline.get('model')}, now {result.model}")
    if baseline.get("spec_sha256") != result.spec_sha256:
        notes.append("spec differs from the baseline's")
    return Comparison(
        regressions,
        changed,
        sorted(set(passes) - set(base)),
        sorted(set(base) - set(passes)),
        base_rate,
        rate,
        notes,
    )


def _majority(fraction: float) -> bool:
    return fraction >= 2 / 3 - 1e-9


def read_baseline(spec_name: str, folder: Path = BASELINES_DIR) -> dict[str, Any] | None:
    path = folder / f"{spec_name}.json"
    return json.loads(path.read_text()) if path.is_file() else None


def save_baseline(result: SuiteResult, folder: Path = BASELINES_DIR) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{result.spec}.json"
    path.write_text(json.dumps(result.baseline(), indent=2, sort_keys=True) + "\n")
    return path


def save_results(result: SuiteResult, folder: Path = RESULTS_DIR) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = folder / f"{result.spec}-{stamp}.json"
    body = {**result.baseline(), "runs": [asdict(r) for r in result.runs]}
    path.write_text(json.dumps(body, indent=2) + "\n")
    return path


def report(result: SuiteResult, comparison: Comparison) -> str:
    passes, runs = result.task_passes(), result.task_runs()
    lines = [f"== {result.spec} on {result.model}: {result.runs_per_task} runs per task"]
    width = max(map(len, passes), default=0)
    for task, p in passes.items():
        verdict = "PASS" if _majority(p / runs[task]) else "FAIL"
        lines.append(f"  {verdict} {p}/{runs[task]}  {task:<{width}}")
    failed = [r for r in result.runs if not r.passed]
    if failed:
        lines.append("\nFailed runs:")
    for r in failed:
        lines.append(f"  {r.task} run {r.run}: {r.status}, {r.steps} steps")
        if r.error:
            lines.append(f"    error: {r.error}")
        lines += [f"    FAIL {c['kind']}: {c['detail']}" for c in r.checks if not c["passed"]]
        for c in r.calls:
            error = f"  -> ERROR {c['error'][:120]}" if c["error"] else ""
            lines.append(f"    call {c['tool']} {json.dumps(c['arguments'])}{error}")
    lines.append("\nChecks by kind:")
    lines += [f"  {k:<16} {p}/{t}" for k, (p, t) in result.check_kinds().items()]
    task_ok = sum(_majority(p / runs[t]) for t, p in passes.items())
    lines.append(
        f"\nTasks {task_ok}/{len(passes)} pass; runs {result.pass_rate:.1%} pass; "
        f"{sum(r.steps for r in result.runs)} steps; ${result.cost_usd:.4f}"
    )
    lines.append(_comparison_text(comparison))
    return "\n".join(lines)


def _comparison_text(c: Comparison) -> str:
    if c.base_rate is None:
        return "No baseline yet (save one with --save-baseline)."
    lines = [f"vs baseline: run pass rate {c.base_rate:.1%} -> {c.rate:.1%}"]
    lines += [f"  note: {n}" for n in c.notes]
    lines += [f"  REGRESSION {r}" for r in c.regressions]
    lines += [f"  changed    {r}" for r in c.changed]
    if c.new_tasks:
        lines.append(f"  new tasks (not compared): {', '.join(c.new_tasks)}")
    if c.missing_tasks:
        lines.append(f"  not run now: {', '.join(c.missing_tasks)}")
    if c.base_rate is not None and c.base_rate - c.rate > RATE_DROP:
        lines.append(f"  REGRESSION pass rate down more than {RATE_DROP * 100:.0f} points")
    lines.append("RESULT: regression" if c.regressed else "RESULT: no regression")
    return "\n".join(lines)
