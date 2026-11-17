"""handle_run end to end with in-memory adapters: no network, no AWS.

The run is contracts/examples/internal-run.valid.json (issue-triage spec). Every update the worker
sends is checked against the RunUpdate schema in contracts/internal-api.openapi.yaml.
"""

import pytest
from contracts import contract_errors, example_run

from worker.adapters.control_plane_memory import InMemoryControlPlane
from worker.adapters.model_stub import ScriptedModel, final_turn, tool_turn
from worker.adapters.tools_fake import issue_triage_tools
from worker.core.errors import ToolError
from worker.core.handle_run import Deps, handle_run
from worker.core.types import ModelTurn, RunStatus, StopReason

RUN_ID = example_run()["run_id"]
REPO = "acme/payments"
SEARCH = ("github.search_issues", {"repo": REPO, "query": "refund EUR"})
LABEL = ("github.add_label", {"repo": REPO, "issue_number": 42, "label": "bug"})
CLOSE = ("github.close_issue", {"repo": REPO, "issue_number": 42, "reason": "duplicate"})


class World:
    """One run plus its fake surroundings."""

    def __init__(self, clock, turns, *, run_changes=None, tools=None):
        run = example_run()
        for key, value in (run_changes or {}).items():
            run[key] = value
        self.control_plane = InMemoryControlPlane()
        self.control_plane.add_run(run)
        self.model = ScriptedModel(turns)
        self.tools = tools or issue_triage_tools()
        self.deps = Deps(self.control_plane, self.model, self.tools, clock)

    def run(self) -> RunStatus:
        return handle_run(RUN_ID, self.deps)

    @property
    def stored(self):
        return self.control_plane.run_json(RUN_ID)

    def assert_updates_match_contract(self):
        for _, body in self.control_plane.updates:
            assert contract_errors("RunUpdate", body) == [], body

    def tool_results_seen_by_model(self, call_index: int):
        """Tool results in the conversation the model received on its Nth call."""
        return [m.tool_result for m in self.model.calls[call_index].messages if m.tool_result]


def test_spec_runs_to_succeeded(clock):
    world = World(
        clock, [tool_turn(SEARCH), tool_turn(LABEL), final_turn("Labeled bug; no duplicate.")]
    )

    assert world.run() is RunStatus.SUCCEEDED

    assert world.stored["status"] == "SUCCEEDED"
    assert world.stored["steps"] == 5  # 3 model turns + 2 tool calls
    assert world.stored["output"] == "Labeled bug; no duplicate."
    assert [name for name, _ in world.tools.calls] == ["github.search_issues", "github.add_label"]
    assert [body["status"] for _, body in world.control_plane.updates] == ["RUNNING", "SUCCEEDED"]
    world.assert_updates_match_contract()


def test_model_gets_instructions_input_and_allowed_tools(clock):
    world = World(clock, [final_turn("Nothing to do.")])

    world.run()

    first = world.model.calls[0]
    assert first.system == example_run()["spec"]["instructions"]
    assert '"number": 42' in first.messages[0].text
    assert [t.name for t in first.tools] == example_run()["spec"]["tools"]


def test_tool_needing_approval_pauses_without_running(clock):
    world = World(clock, [tool_turn(LABEL), tool_turn(CLOSE)])

    assert world.run() is RunStatus.WAITING_APPROVAL

    pending = world.stored["pending_approval"]
    assert pending == {"step": 4, "tool": "github.close_issue", "arguments": CLOSE[1]}
    assert [name for name, _ in world.tools.calls] == ["github.add_label"]  # close never ran
    world.assert_updates_match_contract()


def test_disallowed_tool_is_refused_and_model_told(clock):
    delete = ("github.delete_repo", {"repo": REPO})
    world = World(clock, [tool_turn(delete), final_turn("Could not delete; stopping.")])

    assert world.run() is RunStatus.SUCCEEDED

    assert world.tools.calls == []
    [result] = world.tool_results_seen_by_model(1)
    assert result.is_error
    assert "not allowed" in result.content


def test_invalid_arguments_are_refused_before_the_call(clock):
    bad_label = ("github.add_label", {"repo": REPO, "issue_number": "forty-two"})
    world = World(clock, [tool_turn(bad_label), tool_turn(LABEL), final_turn("Labeled.")])

    assert world.run() is RunStatus.SUCCEEDED

    assert world.tools.calls == [LABEL]  # only the corrected call ran
    [result] = world.tool_results_seen_by_model(1)
    assert result.is_error
    assert "Invalid arguments" in result.content
    assert "label" in result.content and "issue_number" in result.content


def test_failing_tool_is_reported_to_the_model(clock):
    tools = issue_triage_tools()
    original = tools.call

    def flaky(name, arguments):
        if name == "github.search_issues":
            raise ToolError("GitHub returned 502")
        return original(name, arguments)

    tools.call = flaky
    world = World(
        clock, [tool_turn(SEARCH), final_turn("Search is down; labeled nothing.")], tools=tools
    )

    assert world.run() is RunStatus.SUCCEEDED

    [result] = world.tool_results_seen_by_model(1)
    assert result.is_error
    assert "502" in result.content


def test_step_budget_fails_the_run(clock):
    spec = example_run()["spec"] | {"budget": {"max_steps": 3, "max_usd": 0.1, "max_seconds": 300}}
    world = World(
        clock,
        [tool_turn(SEARCH), tool_turn(LABEL), final_turn("never reached")],
        run_changes={"spec": spec},
    )

    assert world.run() is RunStatus.FAILED

    assert world.stored["failure"]["reason"] == "budget_steps"
    assert world.stored["steps"] == 3
    assert [name for name, _ in world.tools.calls] == ["github.search_issues"]
    world.assert_updates_match_contract()


def test_time_budget_fails_the_run(clock):
    class SlowTools:
        def __init__(self):
            self.inner = issue_triage_tools()

        def describe(self, names):
            return self.inner.describe(names)

        def call(self, name, arguments):
            clock.advance(400)  # longer than max_seconds (300)
            return self.inner.call(name, arguments)

    world = World(clock, [tool_turn(SEARCH), final_turn("never reached")], tools=SlowTools())

    assert world.run() is RunStatus.FAILED

    assert world.stored["failure"]["reason"] == "budget_seconds"


@pytest.mark.parametrize("stop", [StopReason.REFUSAL, StopReason.MAX_TOKENS])
def test_refused_or_cut_off_reply_is_not_a_final_answer(clock, stop):
    world = World(clock, [ModelTurn(stop, text="partial")])

    assert world.run() is RunStatus.FAILED

    assert world.stored["failure"]["reason"] == "error"
    assert "output" not in world.stored
    world.assert_updates_match_contract()


def test_model_crash_fails_the_run(clock):
    world = World(clock, [])  # empty script: the stub raises ModelError

    assert world.run() is RunStatus.FAILED

    assert world.stored["failure"]["reason"] == "error"
    assert "ModelError" in world.stored["failure"]["message"]
    world.assert_updates_match_contract()


@pytest.mark.parametrize("status", ["SUCCEEDED", "FAILED", "WAITING_APPROVAL"])
def test_duplicate_delivery_of_settled_run_does_nothing(clock, status):
    world = World(clock, [final_turn("should not run")], run_changes={"status": status})

    assert world.run() is RunStatus(status)

    assert world.control_plane.updates == []
    assert world.model.calls == []


def test_redelivered_running_run_starts_again(clock):
    world = World(
        clock, [final_turn("Done on retry.")], run_changes={"status": "RUNNING", "steps": 4}
    )

    assert world.run() is RunStatus.SUCCEEDED

    assert world.control_plane.updates[0][1] == {"status": "RUNNING", "steps": 0}
