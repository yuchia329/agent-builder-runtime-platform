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
