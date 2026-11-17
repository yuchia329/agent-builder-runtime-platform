"""Core types round-trip the contract shapes."""

from contracts import contract_errors, example_run

from worker.core.types import (
    Failure,
    FailureReason,
    PendingApproval,
    RunRecord,
    RunStatus,
    RunUpdate,
)


def test_run_record_parses_contract_example():
    run = RunRecord.from_dict(example_run())

    assert run.status is RunStatus.QUEUED
    assert run.spec.budget.max_steps == 20
    assert run.spec.require_approval == frozenset({"github.close_issue"})
    assert run.input["issue"]["number"] == 42


def test_every_update_shape_matches_contract():
    updates = [
        RunUpdate(RunStatus.RUNNING, steps=0),
        RunUpdate(
            RunStatus.WAITING_APPROVAL,
            steps=3,
            pending_approval=PendingApproval(4, "github.close_issue", {"issue_number": 42}),
        ),
        RunUpdate(
            RunStatus.FAILED, steps=20, failure=Failure(FailureReason.BUDGET_STEPS, "20 steps")
        ),
        RunUpdate(RunStatus.SUCCEEDED, steps=5, output="Labeled."),
    ]

    for update in updates:
        assert contract_errors("RunUpdate", update.to_dict()) == [], update


def test_messages_survive_a_json_round_trip():
    import json

    from worker.core.types import Message, ModelTurn, StopReason, ToolCall, ToolResult, Usage

    turn = ModelTurn(
        StopReason.TOOL_USE,
        text="Searching.",
        tool_calls=(ToolCall("call_1", "github.search_issues", {"query": "CSV", "n": [1, 2]}),),
        usage=Usage(10, 2),
        provider_data={"reasoning": [{"text": "think"}]},
    )
    messages = [
        Message.user("Run input"),
        Message.assistant(turn),
        Message.tool(ToolResult("call_1", {"items": [{"number": 3}]})),
        Message.tool(ToolResult("call_2", "Tool failed: 404", is_error=True)),
    ]

    restored = [Message.from_json(json.loads(json.dumps(m.to_json()))) for m in messages]

    assert restored == messages
    assert Message.user("x").to_json() == {"role": "user", "text": "x"}
