import json

import pytest
import yaml
from test_runner import BUG, CLOSE, DUPLICATE, SEARCH, SUMMARY, TASK
from worker.adapters.model_stub import ScriptedModel, StubModel, final_turn, tool_turn
from worker.core.errors import ModelError

from evals import judge as judging
from evals import labels, suite
from evals.judge import Judge, Verdict, calibration, context_of, parse


def reply(verdict: str, why: str = "x") -> str:
    return json.dumps({"verdict": verdict, "why": why})


def test_judge_sees_rubric_inputs_and_answer_and_reads_the_verdict():
    model = ScriptedModel(
        [final_turn("Here you go:\n```json\n" + reply("bad", "names #7") + "\n```")]
    )

    verdict = Judge(model, "RUBRIC TEXT").grade(context_of(TASK), "Label: bug")

    assert (verdict.verdict, verdict.why) == ("bad", "names #7")
    assert verdict.cost_usd > 0
    [call] = model.calls
    assert call.model == judging.JUDGE_MODEL and call.tools == ()
    assert call.system.startswith("RUBRIC TEXT")
    prompt = call.messages[0].text
    assert "#5 Crash when uploading a large PNG (open; labels: bug)" in prompt
    assert "App closes itself when I upload a 6 MB PNG image" in prompt
    assert "<<<ANSWER\nLabel: bug\nANSWER>>>" in prompt


def test_context_lists_the_other_issues_not_the_new_one():
    context = context_of(TASK)

    assert context["new_issue"]["number"] == 20 - 10  # this task's new issue is #10
    assert all(not line.startswith("#10 ") for line in context["existing_issues"])
    assert context["repository_labels"] == [
        "bug",
        "enhancement",
        "question",
        "documentation",
        "duplicate",
    ]
    assert tuple(context) == judging.CONTEXT_KEYS


@pytest.mark.parametrize("text", ["no json here", '{"verdict": "maybe"}', "{broken", ""])
def test_unreadable_replies_are_errors_not_verdicts(text):
    assert parse(text).verdict == "error"


def test_missing_answer_is_bad_without_a_model_call():
    model = ScriptedModel([])

    assert Judge(model, "r").grade({}, None).verdict == "bad"
    assert model.calls == []


def test_judge_call_failure_is_an_error_verdict():
    class Broken:
        def complete(self, *args):
            raise ModelError("down")

    assert Judge(Broken(), "r").grade({}, "answer").verdict == "error"


def test_labeled_items_are_graded_in_order_with_their_stored_context():
    class ByAnswer:
        """Says bad when the answer under the marker holds the word wrong."""

        def complete(self, model, system, messages, tools):
            prompt = messages[0].text
            assert '"repository_labels"' in prompt and '"note"' not in prompt
            return final_turn(reply("bad" if "wrong" in prompt.split("<<<ANSWER")[1] else "good"))

    items = [
        {
            "id": n,
            "task": "t",
            "new_issue": {"title": "x"},
            "repository_labels": ["bug"],
            "existing_issues": [],
            "answer": answer,
            "label": "good",
            "note": "wrong",
        }
        for n, answer in enumerate(["fine", "wrong one", "fine too", "also wrong"], 1)
    ]

    verdicts = judging.grade_labeled(Judge(ByAnswer(), "r"), items, parallel=3)

    assert [v.verdict for v in verdicts] == ["good", "bad", "good", "bad"]


def pairs(good_ok: int, good_wrong: int, bad_ok: int, bad_wrong: int):
    return (
        [("good", Verdict("good", ""))] * good_ok
        + [("good", Verdict("bad", ""))] * good_wrong
        + [("bad", Verdict("bad", ""))] * bad_ok
        + [("bad", Verdict("good", ""))] * bad_wrong
    )


@pytest.mark.parametrize(
    ("counts", "trusted"),
    [
        ((40, 1, 9, 0), True),  # 98%, all bad caught
        ((36, 0, 8, 6), False),  # 88% agreement
        ((44, 0, 4, 2), False),  # 96%, but 4 of 6 bad caught (67%)
        ((46, 0, 4, 0), False),  # only 4 bad labels
    ],
)
def test_trust_needs_agreement_enough_bad_labels_and_bad_caught(counts, trusted):
    assert calibration("s", "rubric", "m/x", pairs(*counts))["trusted"] is trusted


def test_trust_is_tied_to_the_rubric_text_and_model(tmp_path):
    judging.save_calibration(calibration("s", "rubric", "m/x", pairs(40, 1, 9, 0)), tmp_path)

    assert judging.trusted("s", "rubric", "m/x", tmp_path)
    assert not judging.trusted("s", "rubric changed", "m/x", tmp_path)
    assert not judging.trusted("s", "rubric", "m/other", tmp_path)
    assert not judging.trusted("other", "rubric", "m/x", tmp_path)


GOOD_RUN = [
    tool_turn(SEARCH),
    tool_turn(BUG),
    tool_turn(DUPLICATE),
    tool_turn(CLOSE),
    final_turn(SUMMARY),
]


class Router:
    """The agent's turns for the agent, a fixed verdict for the judge (told apart by its rubric:
    both use the same model here, as in the real suite)."""

    def __init__(self, verdict: str) -> None:
        self.agent = StubModel(GOOD_RUN)
        self.verdict = verdict

    def complete(self, model, system, messages, tools):
        if system.startswith("JUDGE RUBRIC"):
            return final_turn(reply(self.verdict, "vague reason"))
        return self.agent.complete(model, system, messages, tools)


@pytest.mark.parametrize(("counted", "passed"), [(False, True), (True, False)])
def test_bad_verdict_fails_a_run_only_when_the_judge_counts(counted, passed):
    router = Router("bad")
    judge = Judge(router, "JUDGE RUBRIC")

    r = suite.run_suite([TASK], router, runs=1, parallel=1, judge=judge, judge_counted=counted)
    text = suite.report(r, suite.compare(r, None))

    [run] = r.runs
    assert run.passed is passed
    assert run.judge == {"verdict": "bad", "why": "vague reason", "counted": counted}
    assert r.judged()["bad"] == 1
    if counted:
        assert "FAIL judge: vague reason" in text and "Judge (counted)" in text
    else:
        assert "Judge (not counted until trusted)" in text and "bad: dup-png" in text


def write_results(folder, spec_sha: str, runs: list[tuple[str, bool, str]], name: str):
    body = {
        "spec_sha256": spec_sha,
        "runs": [{"task": t, "run": 1, "passed": p, "output": o} for t, p, o in runs],
    }
    (folder / f"issue-triage-{name}.json").write_text(json.dumps(body))


def test_labels_sample_mixes_current_and_other_spec_runs_without_repeats(tmp_path):
    current = suite.spec_hash(TASK.spec)
    write_results(
        tmp_path,
        current,
        [
            (TASK.name, True, "answer A"),
            (TASK.name, False, "answer B"),
            (TASK.name, True, "answer A"),
            ("plain-question-proxy", True, "answer C"),
        ],
        "1",
    )
    write_results(
        tmp_path, "weakened", [(TASK.name, False, "answer D"), (TASK.name, True, "answer E")], "2"
    )

    items = labels.sample("issue-triage", tmp_path)

    answers = sorted(i["answer"] for i in items)
    assert answers == [
        "answer A",
        "answer B",
        "answer C",
        "answer D",
    ]  # one other-spec run per task
    assert [i["id"] for i in items] == [1, 2, 3, 4]
    assert all(i["label"] is None and "passed" not in i for i in items)
    assert items[0]["existing_issues"] and items[0]["new_issue"]["title"]


def test_labels_file_round_trip_and_bad_label_refused(tmp_path):
    items = [
        {
            "id": 1,
            "task": "t",
            "new_issue": {},
            "existing_issues": [],
            "answer": "a\nb",
            "label": None,
            "note": "",
        }
    ]
    path = labels.write("issue-triage", items, tmp_path)

    assert "answer: |" in path.read_text()
    assert labels.read("issue-triage", tmp_path)[0]["answer"] == "a\nb"
    path.write_text(path.read_text().replace("label: null", "label: Good"))
    with pytest.raises(ValueError, match="good, bad or empty"):
        labels.read("issue-triage", tmp_path)
    assert yaml.safe_load(path.read_text())[0]["id"] == 1
