"""LLM judge for an agent's free-text answer (docs/architecture.md → Testing, ADR 0016).

A spec has a judge when `evals/rubrics/<spec>.md` exists: plain-language criteria its author
writes. The judge model reads the rubric, the run's input (for GitHub tasks: the new issue and
the titles of the existing issues) and the answer, and replies with JSON
`{"verdict": "good" | "bad", "why": "..."}`. It is called through the worker's model adapters.

The judge counts toward pass or fail only once it is trusted: `evals judge-check <spec>` compares
it with the user's labels (`evals/labels/<spec>.yaml`) and writes `evals/judges/<spec>.json`;
trusted means at least 90% agreement, at least `MIN_BAD` bad labels and at least 80% of them
caught, for this rubric text and judge model. Until then its verdicts are only reported.
"""

from __future__ import annotations

import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from worker.core.models import model_info
from worker.core.types import Message
from worker.ports.model import Model

from evals.task import EVALS_DIR, Task

JUDGE_MODEL = "minimax/MiniMax-M3"
RUBRICS_DIR = EVALS_DIR / "rubrics"
JUDGES_DIR = EVALS_DIR / "judges"
MIN_AGREEMENT = 0.9
MIN_BAD = 5
MIN_BAD_CAUGHT = 0.8

FORMAT = """
Reply with one JSON object and nothing else:
{"verdict": "good" or "bad", "why": "<one sentence naming the deciding criterion>"}
"""


@dataclass(frozen=True)
class Verdict:
    verdict: str  # good | bad | error (the judge's reply could not be read)
    why: str
    cost_usd: float = 0.0


def rubric_path(spec: str, folder: Path = RUBRICS_DIR) -> Path:
    return folder / f"{spec}.md"


def read_rubric(spec: str, folder: Path = RUBRICS_DIR) -> str | None:
    path = rubric_path(spec, folder)
    return path.read_text() if path.is_file() else None


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


CONTEXT_KEYS = ("new_issue", "repository_labels", "existing_issues")


def context_of(task: Task) -> dict[str, Any]:
    """What the judge sees besides the answer (keys: CONTEXT_KEYS): the run input, the
    repository's labels, and every existing issue's title, state and labels. That is what the
    agent could see too: the repository's labels are in a tool description, and search results
    carry title, state and labels, not bodies. The judge does not see the agent's tool calls."""
    issue = task.input.get("issue", {})
    new = issue.get("number")
    github = task.world.get("github", {})
    existing = [
        f"#{i['number']} {i['title']} "
        f"({i.get('state', 'open')}; labels: {', '.join(i.get('labels', [])) or 'none'})"
        for i in github.get("issues", [])
        if i["number"] != new
    ]
    return {
        "new_issue": {"number": new, "title": issue.get("title"), "body": issue.get("body", "")},
        "repository_labels": list(github.get("labels", [])),
        "existing_issues": existing,
    }


class Judge:
    def __init__(self, model: Model, rubric: str, model_id: str = JUDGE_MODEL) -> None:
        self._model = model
        self.rubric = rubric
        self.model_id = model_id

    def grade(self, context: dict[str, Any], answer: str | None) -> Verdict:
        if not answer:
            return Verdict("bad", "no final answer")
        prompt = (
            "Inputs the agent had:\n"
            + json.dumps(context, indent=2, ensure_ascii=False)
            + "\n\nThe agent's final answer, between the markers:\n<<<ANSWER\n"
            + answer
            + "\nANSWER>>>"
        )
        try:
            turn = self._model.complete(
                self.model_id, self.rubric + FORMAT, [Message.user(prompt)], []
            )
        except Exception as e:  # noqa: BLE001 - a judge failure is reported, never fatal
            return Verdict("error", f"judge call failed: {type(e).__name__}")
        cost = model_info(self.model_id).price.cost_usd(turn.usage)
        return parse(turn.text, cost)


def parse(text: str, cost_usd: float = 0.0) -> Verdict:
    """The first JSON object in the reply (models sometimes add a sentence or a code fence)."""
    found = re.search(r"\{.*\}", text or "", re.DOTALL)
    try:
        data = json.loads(found.group(0)) if found else None
    except json.JSONDecodeError:
        data = None
    if not isinstance(data, dict) or data.get("verdict") not in ("good", "bad"):
        return Verdict("error", f"unreadable judge reply: {(text or '')[:120]!r}", cost_usd)
    return Verdict(data["verdict"], str(data.get("why", ""))[:300], cost_usd)


def grade_labeled(judge: Judge, items: list[dict[str, Any]], parallel: int = 4) -> list[Verdict]:
    """Verdicts for labeled items (evals/labels/<spec>.yaml), in the items' order."""

    def grade(item: dict[str, Any]) -> Verdict:
        context = {k: item[k] for k in CONTEXT_KEYS if k in item}
        return judge.grade(context, item["answer"])

    with ThreadPoolExecutor(max(1, parallel)) as pool:
        return list(pool.map(grade, items))


# --- trust -------------------------------------------------------------------------------------


def calibration(
    spec: str, rubric: str, model_id: str, pairs: list[tuple[str, Verdict]]
) -> dict[str, Any]:
    """Agreement of (user label, verdict) pairs, and whether the judge is trusted."""
    agree = sum(label == v.verdict for label, v in pairs)
    bad = [v for label, v in pairs if label == "bad"]
    caught = sum(v.verdict == "bad" for v in bad)
    agreement = agree / len(pairs) if pairs else 0.0
    bad_caught = caught / len(bad) if bad else 0.0
    return {
        "spec": spec,
        "model": model_id,
        "rubric_sha256": sha256(rubric),
        "date": datetime.now(UTC).strftime("%Y-%m-%d"),
        "labeled": len(pairs),
        "agreement": round(agreement, 4),
        "bad_labels": len(bad),
        "bad_caught": caught,
        "errors": sum(v.verdict == "error" for _, v in pairs),
        "trusted": agreement >= MIN_AGREEMENT
        and len(bad) >= MIN_BAD
        and bad_caught >= MIN_BAD_CAUGHT,
    }


def save_calibration(result: dict[str, Any], folder: Path = JUDGES_DIR) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{result['spec']}.json"
    path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return path


def trusted(spec: str, rubric: str, model_id: str = JUDGE_MODEL, folder: Path = JUDGES_DIR) -> bool:
    """True only for a saved, trusted calibration of this exact rubric and judge model."""
    path = folder / f"{spec}.json"
    if not path.is_file():
        return False
    saved = json.loads(path.read_text())
    return (
        saved.get("trusted") is True
        and saved.get("rubric_sha256") == sha256(rubric)
        and saved.get("model") == model_id
    )
