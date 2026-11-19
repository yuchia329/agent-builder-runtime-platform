import json

from test_runner import BUG, CLOSE, DUPLICATE, SEARCH, SUMMARY, TASK
from worker.adapters.model_stub import StubModel, final_turn, tool_turn

from evals import suite
from evals.suite import RunSummary, SuiteResult, compare


def result(
    passes: dict[str, int], runs: int = 3, model: str = "m/x", sha: str = "s"
) -> SuiteResult:
    r = SuiteResult("issue-triage", model, sha, runs)
    for task, p in passes.items():
        for n in range(1, runs + 1):
            r.runs.append(RunSummary(task, n, n <= p, "SUCCEEDED", 5, 0.001, [], [], "x"))
    return r


def baseline(passes: dict[str, int], **kw) -> dict:
    return result(passes, **kw).baseline()


def test_task_regresses_only_on_a_drop_of_two_thirds_or_more():
    base = baseline({"a": 3, "b": 3, "c": 2, "d": 2, "e": 3, "f": 1})
    now = result({"a": 1, "b": 0, "c": 0, "d": 1, "e": 2, "f": 2})

    c = compare(now, base)

    assert c.regressions == ["a: 3/3 -> 1/3", "b: 3/3 -> 0/3", "c: 2/3 -> 0/3"]
    assert c.changed == ["d: 2/3 -> 1/3", "f: 1/3 -> 2/3"]  # e still passes: not listed
    assert c.regressed


def test_suite_regresses_on_a_pass_rate_drop_of_more_than_five_points():
    tasks = {f"t{i}": 3 for i in range(10)}
    base = baseline(tasks)
    two_flips = result(tasks | {"t0": 2, "t1": 2})  # 28/30: 6.7 points down, no task regresses
    one_flip = result(tasks | {"t0": 2})  # 29/30: 3.3 points down

    assert compare(two_flips, base).regressions == []
    assert compare(two_flips, base).regressed
    assert not compare(one_flip, base).regressed


def test_only_tasks_in_both_are_compared_and_differences_are_noted():
    base = baseline({"a": 3, "gone": 0}, model="m/old", sha="old")
    now = result({"a": 3, "new": 0})

    c = compare(now, base)

    assert (c.new_tasks, c.missing_tasks) == (["new"], ["gone"])
    assert (c.base_rate, c.rate) == (1.0, 1.0)
    assert not c.regressed
    assert c.notes == ["model differs: baseline m/old, now m/x", "spec differs from the baseline's"]


def test_without_a_baseline_nothing_regresses():
    c = compare(result({"a": 0}), None)

    assert not c.regressed
    assert "No baseline yet" in suite.report(result({"a": 0}), c)


def test_baseline_round_trip(tmp_path):
    r = result({"a": 2, "b": 3})

    suite.save_baseline(r, tmp_path)
    saved = suite.read_baseline("issue-triage", tmp_path)

    assert saved["tasks"] == {"a": {"passes": 2, "runs": 3}, "b": {"passes": 3, "runs": 3}}
    assert saved["pass_rate"] == round(5 / 6, 4)
    assert suite.read_baseline("other", tmp_path) is None


def test_suite_runs_every_task_several_times_in_parallel(tmp_path):
    good = StubModel(
        [
            tool_turn(SEARCH),
            tool_turn(BUG),
            tool_turn(DUPLICATE),
            tool_turn(CLOSE),
            final_turn(SUMMARY),
        ]
    )
    seen = []

    r = suite.run_suite([TASK], good, runs=3, parallel=2, progress=lambda s, d, t: seen.append(d))

    assert seen == [1, 2, 3]
    assert [(x.task, x.run, x.passed) for x in r.runs] == [(TASK.name, n, True) for n in (1, 2, 3)]
    assert r.task_passes() == {TASK.name: 3}
    assert r.check_kinds()["github"] == (3 * 2, 3 * 2)
    assert r.spec_sha256 == suite.spec_hash(TASK.spec)
    saved = json.loads(suite.save_results(r, tmp_path).read_text())
    assert len(saved["runs"]) == 3 and saved["runs"][0]["calls"][0]["tool"] == SEARCH[0]


def test_report_shows_failed_runs_with_their_tool_calls():
    bad = StubModel(
        [tool_turn(SEARCH), final_turn("Label: bug\nDuplicate of: none found\nClosed: no")]
    )

    r = suite.run_suite([TASK], bad, runs=1, parallel=1)
    text = suite.report(r, compare(r, baseline({TASK.name: 3}, runs=1)))

    assert f"FAIL 0/1  {TASK.name}" in text
    assert 'call github.search_issues {"q": "PNG upload"}' in text
    assert f"REGRESSION {TASK.name}: 1/1 -> 0/1" in text
    assert text.endswith("RESULT: regression")
