"""Hand labels for calibrating a spec's judge: `evals/labels/<spec>.yaml`.

`sample` picks answers from saved suite results (`evals/results/`): about 35 from runs of the
current spec (one per task, then failed runs) and about 15 from runs of other versions of the
spec (a weakened copy run with --specs-dir, mostly failed runs), so the file holds enough bad
answers to test the judge. Each item carries what the judge sees (the new issue, the existing
issues) and the answer; the user fills in `label: good` or `label: bad`. The file never says
whether the run passed its checks, so labels are not anchored on them.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

import yaml

from evals.judge import context_of
from evals.suite import RESULTS_DIR, TASKS_DIR, spec_hash
from evals.task import EVALS_DIR, load_spec, load_task

LABELS_DIR = EVALS_DIR / "labels"
CURRENT = 35
OTHER = 15

HEADER = """\
# Hand labels for the {spec} judge (evals judge-check {spec}). For each item, read the new
# issue, the existing issues and the agent's answer, then set label to good or bad using
# evals/rubrics/{spec}.md. A note is optional. Leave label empty to skip an item.
"""


class _Dumper(yaml.SafeDumper):
    def ignore_aliases(self, data: Any) -> bool:
        return True


def _text(dumper: yaml.SafeDumper, value: str) -> yaml.Node:
    style = "|" if "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_Dumper.add_representer(str, _text)


def _runs(spec: str, folder: Path) -> tuple[list[dict], list[dict]]:
    """(runs of the current spec, runs of other versions), with outputs, deduplicated."""
    current_hash = spec_hash(load_spec(spec))
    current: list[dict] = []
    other: list[dict] = []
    seen: set[str] = set()
    for path in sorted(folder.glob(f"{spec}-*.json")):
        body = json.loads(path.read_text())
        target = current if body.get("spec_sha256") == current_hash else other
        for run in body["runs"]:
            output = (run.get("output") or "").strip()
            if output and output not in seen:
                seen.add(output)
                target.append(run)
    return current, other


def _pick(runs: list[dict], count: int, rng: random.Random, first_one_per_task: bool) -> list[dict]:
    pool = runs[:]
    rng.shuffle(pool)
    chosen: list[dict] = []
    if first_one_per_task:
        tasks: set[str] = set()
        for run in pool:
            if run["task"] not in tasks and len(chosen) < count:
                tasks.add(run["task"])
                chosen.append(run)
    failed = [r for r in pool if not r["passed"] and r not in chosen]
    rest = [r for r in pool if r["passed"] and r not in chosen]
    for run in failed + rest:
        if len(chosen) >= count:
            break
        if first_one_per_task or run["task"] not in {c["task"] for c in chosen}:
            chosen.append(run)
    return chosen


def sample(spec: str, results: Path = RESULTS_DIR, seed: int = 0) -> list[dict[str, Any]]:
    current, other = _runs(spec, results)
    if not current:
        raise FileNotFoundError(f"No saved results of the current {spec} spec in {results}")
    rng = random.Random(seed)
    picked = _pick(current, CURRENT, rng, True) + _pick(other, OTHER, rng, False)
    rng.shuffle(picked)
    items = []
    for n, run in enumerate(picked, 1):
        task = load_task(TASKS_DIR / spec / f"{run['task']}.yaml")
        items.append(
            {
                "id": n,
                "task": run["task"],
                **context_of(task),
                "answer": run["output"].strip(),
                "label": None,
                "note": "",
            }
        )
    return items


def write(spec: str, items: list[dict[str, Any]], folder: Path = LABELS_DIR) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{spec}.yaml"
    body = yaml.dump(items, Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=100)
    path.write_text(HEADER.format(spec=spec) + body)
    return path


def read(spec: str, folder: Path = LABELS_DIR) -> list[dict[str, Any]]:
    path = folder / f"{spec}.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"No labels file {path} (make one with: evals labels {spec})")
    items = yaml.safe_load(path.read_text()) or []
    bad = [i.get("id") for i in items if i.get("label") not in (None, "", "good", "bad")]
    if bad:
        raise ValueError(f"{path}: label must be good, bad or empty (items {bad})")
    return items
