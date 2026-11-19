"""A task end to end with a scripted model: the real gateway and its GitHub connector against the
simulator, approvals answered automatically, checks evaluated. No model provider is called."""

from worker.adapters.model_stub import ScriptedModel, final_turn, tool_turn

from evals.task import EVALS_DIR, load_task

TASK = load_task(EVALS_DIR / "tasks" / "issue-triage" / "dup-png-upload-crash.yaml")
REPO = {"owner": "octocat", "repo": "Hello-World"}
SEARCH = ("github.search_issues", {"q": "PNG upload"})
BUG = ("github.add_label", {**REPO, "issue_number": 10, "labels": ["bug"]})
DUPLICATE = ("github.add_label", {**REPO, "issue_number": 10, "labels": ["duplicate"]})
CLOSE = (
    "github.close_issue",
    {**REPO, "issue_number": 10, "state_reason": "duplicate", "duplicate_of": 5},
)
SUMMARY = (
    "Label: bug\nDuplicate of: #5 (same large PNG upload crash)\nConfidence: high\nClosed: yes"
)


def failed_checks(result) -> list[str]:
    return [f"{c.kind}: {c.detail}" for c in result.checks if not c.passed]


def test_good_run_passes_every_check(evaluation):
    model = ScriptedModel(
        [
            tool_turn(SEARCH),
            tool_turn(BUG),
            tool_turn(DUPLICATE),
            tool_turn(CLOSE),
            final_turn(SUMMARY),
        ]
    )

    result = evaluation.run(TASK, model)

    assert failed_checks(result) == []
    assert result.passed
    outcome = result.outcome
    assert [c.tool for c in outcome.calls] == [SEARCH[0], BUG[0], DUPLICATE[0], CLOSE[0]]
    assert outcome.calls[0].error is None
    assert [a["tool"] for a in outcome.approvals] == ["github.close_issue"]
    issue = outcome.world["github"]["issues"][10]
    assert (issue["state"], issue["duplicate_of"]) == ("closed", 5)


def test_search_through_the_gateway_reaches_the_simulator(evaluation):
    model = ScriptedModel([tool_turn(SEARCH), final_turn("done")])

    evaluation.run(TASK, model)

    sent = model.calls[1].messages[-1].tool_result
    numbers = [item["number"] for item in sent.content["items"]]
    assert numbers == [10]  # whole words: #5 says "uploading", not "upload"
    assert set(sent.content["items"][0]) == {"number", "title", "state", "labels", "url"}


def test_wrong_decisions_fail_the_matching_checks(evaluation):
    wrong_close = ("github.close_issue", {**CLOSE[1], "issue_number": 5, "duplicate_of": 10})
    model = ScriptedModel(
        [
            tool_turn(
                ("github.add_label", {**REPO, "issue_number": 10, "labels": ["enhancement"]})
            ),
            tool_turn(wrong_close),
            final_turn("Label: enhancement\nDuplicate of: none found\nClosed: no"),
        ]
    )

    result = evaluation.run(TASK, model)

    failed = {c.kind for c in result.checks if not c.passed}
    assert failed == {"github", "tool_called", "tool_not_called", "output"}
    assert result.outcome.status == "SUCCEEDED"
    assert not result.passed


def test_tool_error_reaches_the_model_and_counts_as_not_called(evaluation):
    missing = ("github.add_label", {**REPO, "issue_number": 10, "labels": ["crash"]})
    model = ScriptedModel([tool_turn(missing), final_turn("gave up")])

    result = evaluation.run(TASK, model)

    [call] = result.outcome.calls
    assert "does not exist" in call.error
    assert result.outcome.world["github"]["issues"][10]["labels"] == []


def test_each_task_starts_from_its_own_world(evaluation):
    evaluation.run(TASK, ScriptedModel([tool_turn(BUG), final_turn("x")]))
    result = evaluation.run(TASK, ScriptedModel([final_turn("nothing")]))

    assert result.outcome.world["github"]["issues"][10]["labels"] == []


def test_budget_exceeded_is_a_failed_status(evaluation):
    model = ScriptedModel([tool_turn(SEARCH)] * 30)

    result = evaluation.run(TASK, model)

    assert result.outcome.status == "FAILED"
    assert result.outcome.failure["reason"] == "budget_steps"
    status = next(c for c in result.checks if c.kind == "status")
    assert not status.passed and "budget_steps" in status.detail
