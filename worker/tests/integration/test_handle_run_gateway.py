"""handle_run with GatewayTools against the real tool gateway (fake connector): the run token,
step and idempotency key travel the whole way, and the gateway's flags reach core."""

from contracts import example_run
from test_handle_run import LABEL, SEARCH, World

from worker.adapters.model_stub import final_turn, tool_turn
from worker.adapters.tools_mcp import GatewayTools
from worker.core.types import RunStatus


def test_run_uses_tools_through_the_gateway(clock, live_gateway, run_tokens):
    run = example_run()
    token = run_tokens.sign(
        run["run_id"],
        tools=run["spec"]["tools"],
        require_approval=run["spec"].get("require_approval", []),
    )
    world = World(
        clock,
        [tool_turn(SEARCH), tool_turn(LABEL), final_turn("Labeled bug; no duplicate.")],
        run_changes={"run_token": token},
        tools=GatewayTools(live_gateway.url),
    )

    assert world.run() is RunStatus.SUCCEEDED

    results = world.tool_results_seen_by_model(2)
    assert [r.is_error for r in results] == [False, False]
    assert results[0].content == {"items": [{"number": 7, "title": "Refunds fail for EUR cards"}]}
    assert world.stored["steps"] == 5


def test_run_without_a_token_fails_at_start_with_the_gateway(clock, live_gateway):
    world = World(clock, [final_turn("never")], tools=GatewayTools(live_gateway.url))

    assert world.run() is RunStatus.FAILED
    assert "no run token" in world.stored["failure"]["message"]
    assert world.model.calls == []
