"""/mcp end to end: a real server, the SDK's client, signed run tokens."""

import json
import logging

import httpx2
import pytest
from gateway_testing import TOOLS, claims, mcp_client, meta, token
from mcp.shared.exceptions import MCPError

pytestmark = pytest.mark.anyio

ARGS = {"repo": "octocat/Hello-World", "issue_number": 8, "label": "bug"}


async def test_no_or_bad_token_is_401_with_a_json_rpc_error(gateway, keys):
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    async with httpx2.AsyncClient() as http:
        for headers in ({}, {"Authorization": "Bearer nope"}, {"Authorization": "Basic x"}):
            response = await http.post(f"{gateway.url}/mcp", json=body, headers=headers)

            assert response.status_code == 401
            assert response.headers["www-authenticate"] == "Bearer"
            assert response.json()["error"] == {"code": -32001, "message": "Unauthorized"}


async def test_health_needs_no_token(gateway):
    async with httpx2.AsyncClient() as http:
        assert (await http.get(f"{gateway.url}/health")).json() == {"status": "ok"}


async def test_tools_list_shows_only_the_tokens_tools_with_annotations(gateway, keys):
    async with mcp_client(
        gateway, token(keys, claims(tools=[*TOOLS[1:3], "notion.search"]))
    ) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}

    assert set(tools) == {"github.add_label", "github.close_issue"}  # notion.search is not loaded
    close = tools["github.close_issue"].annotations
    assert close.idempotent_hint and close.destructive_hint and not close.read_only_hint
    assert tools["github.add_label"].input_schema["required"] == ["repo", "issue_number", "label"]


async def test_call_runs_the_tool_and_returns_structured_content(gateway, keys, fake):
    async with mcp_client(gateway, token(keys)) as client:
        result = await client.call_tool("github.add_label", ARGS, meta=meta())

    assert not result.is_error
    assert result.structured_content == {"ok": True}
    assert json.loads(result.content[0].text) == {"ok": True}
    assert fake.calls == [("github.add_label", ARGS, "run_test")]


async def test_tool_not_in_the_token_is_a_protocol_error(gateway, keys, fake):
    async with mcp_client(gateway, token(keys)) as client:
        with pytest.raises(MCPError, match="Unknown tool"):
            await client.call_tool(
                "slack.post_message", {"channel": "C1", "text": "x"}, meta=meta()
            )

    assert fake.calls == []


async def test_tool_in_the_token_but_not_loaded_is_a_protocol_error(gateway, keys):
    async with mcp_client(gateway, token(keys, claims(tools=["notion.search"]))) as client:
        with pytest.raises(MCPError, match="Unknown tool"):
            await client.call_tool("notion.search", {}, meta=meta())


@pytest.mark.parametrize(
    "bad_meta",
    [None, {"agentplatform/step": 3}, meta(step=0), meta(key="short")],
    ids=["none", "no key", "step 0", "short key"],
)
async def test_call_without_valid_meta_is_a_protocol_error(gateway, keys, fake, bad_meta):
    async with mcp_client(gateway, token(keys)) as client:
        with pytest.raises(MCPError, match="Invalid tools/call params"):
            await client.call_tool("github.add_label", ARGS, meta=bad_meta)

    assert fake.calls == []


async def test_bad_arguments_are_a_tool_error_the_model_can_read(gateway, keys, fake):
    async with mcp_client(gateway, token(keys)) as client:
        result = await client.call_tool(
            "github.add_label", {**ARGS, "issue_number": "eight", "extra": 1}, meta=meta()
        )

    assert result.is_error
    assert "Invalid arguments" in result.content[0].text
    assert fake.calls == []


async def test_approval_tools_are_refused_without_a_control_plane(gateway, keys, fake):
    approval = claims(tools=["github.close_issue"], require_approval=["github.close_issue"])
    async with mcp_client(gateway, token(keys, approval)) as client:
        result = await client.call_tool(
            "github.close_issue", {"repo": "octocat/Hello-World", "issue_number": 8}, meta=meta()
        )

    assert result.is_error
    assert fake.calls == []


async def test_every_call_is_access_logged_without_values_or_token(gateway, keys, caplog):
    run_token = token(keys)
    with caplog.at_level(logging.INFO, logger="gateway.access"):
        async with mcp_client(gateway, run_token) as client:
            await client.call_tool("github.add_label", ARGS, meta=meta(step=5, key="b" * 64))
            await client.call_tool("github.add_label", {"repo": 1}, meta=meta(step=6))

    lines = [json.loads(r.getMessage()) for r in caplog.records if r.name == "gateway.access"]
    assert [line["outcome"] for line in lines] == ["ok", "error"]
    first = lines[0]
    assert first["run_id"] == "run_test"
    assert first["tenant"] == "tenant-test"
    assert first["agent"] == "issue-triage@4"
    assert first["started_by"] == "webhook:github"
    assert first["acts_as"] == "team-bot" and first["identity"] == "team-bot"
    assert first["tool"] == "github.add_label"
    assert first["step"] == 5 and first["idempotency_key"] == "b" * 64
    assert len(first["arguments_sha256"]) == 64
    logged = caplog.text
    assert run_token not in logged
    assert "octocat/Hello-World" not in logged and "bug" not in logged


async def test_owner_runs_log_the_owner_as_identity(gateway, keys, caplog):
    owner = claims(acts_as="owner", owner="user:42")
    with caplog.at_level(logging.INFO, logger="gateway.access"):
        async with mcp_client(gateway, token(keys, owner)) as client:
            await client.call_tool("github.add_label", ARGS, meta=meta())

    line = json.loads(next(r.getMessage() for r in caplog.records if r.name == "gateway.access"))
    assert line["identity"] == "user:42"


async def test_generated_github_tools_work_through_mcp(keys):
    from fake_github import REPO, FakeGitHub, app_credential, fixture
    from gateway_testing import CONTRACTS, LiveGateway

    from gateway.adapters.token_store_memory import MemoryTokenStore
    from gateway.config import CONNECTORS_FOLDER
    from gateway.credentials import Credentials
    from gateway.ports.token_store import CredentialKey
    from gateway.tools.executor import ConnectorClient
    from gateway.tools.openapi_to_mcp import load_connector

    store = MemoryTokenStore()
    store.put(CredentialKey("tenant-test", "team-bot", "github"), app_credential())
    definition = load_connector(CONNECTORS_FOLDER / "github", CONTRACTS, {"GITHUB_REPOS": REPO})
    with FakeGitHub() as github:
        github.on("GET", "/search/issues", fixture("search_issues"))
        connector = ConnectorClient(definition, Credentials(store), base_url=github.url)
        with LiveGateway(keys, connector) as gateway:
            async with mcp_client(gateway, token(keys)) as client:
                tools = {t.name: t for t in (await client.list_tools()).tools}
                result = await client.call_tool(
                    "github.search_issues", {"q": "refund"}, meta=meta()
                )

    assert set(tools) == {"github.search_issues", "github.add_label"}
    assert tools["github.search_issues"].annotations.read_only_hint
    assert not result.is_error
    assert result.structured_content["items"][0].keys() == {
        "number",
        "title",
        "state",
        "labels",
        "url",
    }


async def test_listing_keeps_plain_descriptions_when_github_fails(keys):
    from fake_github import REPO, FakeGitHub, app_credential
    from gateway_testing import CONTRACTS, LiveGateway

    from gateway.adapters.token_store_memory import MemoryTokenStore
    from gateway.config import CONNECTORS_FOLDER
    from gateway.credentials import Credentials
    from gateway.ports.token_store import CredentialKey
    from gateway.tools.executor import ConnectorClient
    from gateway.tools.openapi_to_mcp import load_connector

    store = MemoryTokenStore()
    store.put(CredentialKey("tenant-test", "team-bot", "github"), app_credential())
    definition = load_connector(CONNECTORS_FOLDER / "github", CONTRACTS, {"GITHUB_REPOS": REPO})
    with FakeGitHub() as github:  # no labels route: the listing call gets 404
        connector = ConnectorClient(definition, Credentials(store), base_url=github.url)
        with LiveGateway(keys, connector) as gateway:
            async with mcp_client(gateway, token(keys)) as client:
                tools = {t.name: t for t in (await client.list_tools()).tools}

    assert tools["github.add_label"].description == definition.tools["github.add_label"].description
