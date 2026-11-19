"""Second approval check (ADR 0003): unit cases against an in-memory control plane, then end to
end through /mcp with the fake connector."""

import json
import logging

import pytest
from gateway_testing import LiveGateway, claims, mcp_client, meta, token

from gateway.adapters.control_plane_memory import MemoryControlPlane
from gateway.approvals import ApprovalCheck, canonical
from gateway.auth.run_token import RunClaims

TOOL = "github.close_issue"
ARGS = {"repo": "octocat/Hello-World", "issue_number": 8, "reason": "duplicate"}
STEP = 7
CLAIMS = claims(tools=[TOOL], require_approval=[TOOL])


def approval(**overrides):
    base = {
        "step": STEP,
        "tool": TOOL,
        "arguments": dict(ARGS),
        "decision": "approved",
        "decided_by": "U0123",
        "decided_at": "2026-11-18T10:00:00Z",
    }
    return {**base, **overrides}


def run(**overrides):
    base = {
        "run_id": "run_test",
        "tenant_id": "tenant-test",
        "agent_name": "issue-triage",
        "agent_version": 4,
        "status": "RUNNING",
        "approval": approval(),
        "run_token": "eyJ.not.kept",
    }
    return {k: v for k, v in {**base, **overrides}.items() if v is not None}


def approved_args(arguments):
    return run(approval=approval(arguments=arguments))


OTHER_ARGS = "approval for other arguments"


def check(stored=None, arguments=ARGS, step=STEP, tool=TOOL, plane=None):
    plane = plane or MemoryControlPlane()
    if stored is not None:
        plane.put(stored)
    return ApprovalCheck(plane).check(RunClaims.from_payload(CLAIMS), tool, step, arguments)


def test_matching_approval_allows_the_call():
    assert check(run()) is None


def test_argument_order_does_not_matter():
    reordered = dict(reversed(list(ARGS.items())))
    assert check(run(), arguments=reordered) is None


@pytest.mark.parametrize(
    ("stored", "reason"),
    [
        (run(approval=None), "no approval"),
        (run(approval=approval(decision="rejected")), "approval not approved"),
        (run(approval=approval(step=6)), "approval for another step"),
        (run(approval=approval(step=True)), "approval for another step"),
        (run(approval=approval(tool="github.add_label")), "approval for another tool"),
        (approved_args({**ARGS, "issue_number": 9}), OTHER_ARGS),
        (approved_args({**ARGS, "issue_number": 8.0}), OTHER_ARGS),
        (approved_args({"repo": ARGS["repo"], "issue_number": 8}), OTHER_ARGS),
        (run(status="WAITING_APPROVAL"), "run not running"),
        (run(status="SUCCEEDED"), "run not running"),
        (run(tenant_id="tenant-other"), "run does not match the token"),
        (run(approval="yes"), "no approval"),
    ],
    ids=[
        "none", "rejected", "other-step", "step-true", "other-tool", "other-value",
        "float-not-int", "missing-argument", "waiting", "finished", "other-tenant", "malformed",
    ],
)  # fmt: skip
def test_anything_but_an_exact_approved_match_is_refused(stored, reason):
    assert check(stored) == reason


def test_missing_run_is_refused():
    assert check(None) == "run not found"


def test_control_plane_down_is_refused():
    plane = MemoryControlPlane()
    plane.put(run())
    plane.unavailable = True

    assert check(plane=plane) == "control plane unavailable"


def test_without_a_control_plane_every_call_is_refused():
    reason = ApprovalCheck(None).check(RunClaims.from_payload(CLAIMS), TOOL, STEP, ARGS)
    assert reason == "approval check not configured"


def test_canonical_json_matches_the_workers():
    # worker/src/worker/core/handle_run.py::_canonical
    assert canonical({"b": [1, True], "a": "é"}) == '{"a":"\\u00e9","b":[1,true]}'


# End to end through /mcp.


@pytest.fixture
def plane():
    return MemoryControlPlane()


@pytest.fixture
def approving_gateway(keys, fake, plane):
    with LiveGateway(keys, fake, approvals=ApprovalCheck(plane)) as live:
        yield live


@pytest.mark.anyio
async def test_approved_call_runs_through_the_gateway(approving_gateway, keys, fake, plane):
    plane.put(run())
    async with mcp_client(approving_gateway, token(keys, CLAIMS)) as client:
        result = await client.call_tool(TOOL, ARGS, meta=meta(step=STEP))

    assert not result.is_error
    assert fake.calls == [(TOOL, ARGS, "run_test")]
    assert plane.reads == ["run_test"]


@pytest.mark.anyio
async def test_refused_call_logs_the_reason_and_tells_the_model_only_that_it_was_refused(
    approving_gateway, keys, fake, plane, caplog
):
    plane.put(run(approval=approval(arguments={**ARGS, "issue_number": 9})))
    with caplog.at_level(logging.INFO, logger="gateway.access"):
        async with mcp_client(approving_gateway, token(keys, CLAIMS)) as client:
            result = await client.call_tool(TOOL, ARGS, meta=meta(step=STEP))

    assert result.is_error
    assert result.content[0].text == (
        f"{TOOL} needs a person's approval for this exact call; no matching approval was found"
    )
    assert fake.calls == []
    (line,) = [json.loads(r.getMessage()) for r in caplog.records if r.name == "gateway.access"]
    assert (line["outcome"], line["reason"]) == ("refused", "approval for other arguments")


@pytest.mark.anyio
async def test_tools_without_approval_do_not_read_the_run(approving_gateway, keys, fake, plane):
    async with mcp_client(approving_gateway, token(keys)) as client:
        await client.call_tool(
            "github.add_label",
            {"repo": ARGS["repo"], "issue_number": 8, "label": "bug"},
            meta=meta(),
        )

    assert plane.reads == []
    assert len(fake.calls) == 1


@pytest.mark.anyio
async def test_invalid_arguments_are_refused_before_reading_the_run(approving_gateway, keys, plane):
    async with mcp_client(approving_gateway, token(keys, CLAIMS)) as client:
        result = await client.call_tool(TOOL, {"repo": 1}, meta=meta(step=STEP))

    assert result.is_error and "Invalid arguments" in result.content[0].text
    assert plane.reads == []
