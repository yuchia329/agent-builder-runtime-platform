"""Every check kind passes on a good outcome and fails, with a reason, on a bad one."""

import pytest

from evals.checks import Outcome, ToolCallRecord, evaluate

CLOSE = {
    "issue_number": 10,
    "state_reason": "duplicate",
    "duplicate_of": 5,
    "owner": "o",
    "repo": "r",
}


def outcome(**changes) -> Outcome:
    base = {
        "status": "SUCCEEDED",
        "failure": None,
        "output": "Label: bug\nDuplicate of: #5 (same crash)\nClosed: yes",
        "steps": 6,
        "cost_usd": 0.004,
        "calls": (
            ToolCallRecord(
                "github.add_label",
                {"issue_number": 10, "labels": ["bugg"]},
                "Label 'bugg' does not exist",
            ),
            ToolCallRecord("github.add_label", {"issue_number": 10, "labels": ["bug"]}),
            ToolCallRecord("github.close_issue", CLOSE),
        ),
        "approvals": (),
        "world": {
            "github": {
                "labels": ["bug", "duplicate"],
                "issues": {
                    10: {
                        "labels": ["bug", "duplicate"],
                        "state": "closed",
                        "state_reason": "duplicate",
                        "duplicate_of": 5,
                    },
                },
            }
        },
    }
    return Outcome(**(base | changes))


GOOD_AND_BAD = [
    (
        {"status": "SUCCEEDED"},
        {"status": "FAILED", "failure": {"reason": "budget_exceeded", "message": "steps"}},
        "budget_exceeded",
    ),
    ({"failure_reason": "budget_exceeded"}, {}, "got none"),
    (
        {"tool_called": {"tool": "github.close_issue", "arguments": {"duplicate_of": 5}}},
        {"calls": ()},
        "none",
    ),
    (
        {"tool_called": {"tool": "github.add_label", "arguments": {"labels": ["bug"]}}},
        {"calls": (ToolCallRecord("github.add_label", {"labels": ["bug"]}, "refused"),)},
        "every matching call failed",
    ),
    (
        {"tool_not_called": {"tool": "github.close_issue", "arguments": {"issue_number": 5}}},
        {"calls": (ToolCallRecord("github.close_issue", {"issue_number": 5}),)},
        "called with",
    ),
    (
        {
            "github": {
                "issue": 10,
                "labels": ["bug", "duplicate"],
                "state": "closed",
                "duplicate_of": 5,
            }
        },
        {
            "world": {
                "github": {
                    "issues": {
                        10: {
                            "labels": ["bug"],
                            "state": "open",
                            "state_reason": None,
                            "duplicate_of": None,
                        }
                    }
                }
            }
        },
        "expected ['bug', 'duplicate']",
    ),
    (
        {"github": {"issue": 10, "labels_include": ["duplicate"], "labels_exclude": ["question"]}},
        {
            "world": {
                "github": {
                    "issues": {
                        10: {
                            "labels": ["question"],
                            "state": "open",
                            "state_reason": None,
                            "duplicate_of": None,
                        }
                    }
                }
            }
        },
        "lack ['duplicate']",
    ),
    ({"github": {"issue": 10}}, {"world": {}}, "not in the simulated repository"),
    (
        {"output": {"matches": ["(?m)^Label: bug"], "not_matches": ["question"]}},
        {"output": "Label: question"},
        "no match",
    ),
    ({"output": {"matches": ["Label"]}}, {"output": None}, "no final answer"),
]


@pytest.mark.parametrize(
    ("check", "bad", "reason"), GOOD_AND_BAD, ids=[next(iter(c[0])) for c in GOOD_AND_BAD]
)
def test_check_passes_on_good_and_fails_on_bad(check, bad, reason):
    good_outcome = (
        outcome(failure={"reason": "budget_exceeded"}) if "failure_reason" in check else outcome()
    )
    good = evaluate(check, good_outcome)
    failed = evaluate(check, outcome(**bad))

    assert good.passed, good.detail
    assert not failed.passed
    assert reason in failed.detail
    assert good.kind == failed.kind == next(iter(check))


def test_argument_match_ignores_keys_not_listed_and_needs_exact_values():
    o = outcome()

    assert evaluate(
        {"tool_called": {"tool": "github.close_issue", "arguments": {"issue_number": 10}}}, o
    ).passed
    assert not evaluate(
        {"tool_called": {"tool": "github.close_issue", "arguments": {"issue_number": "10"}}}, o
    ).passed
