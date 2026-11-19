"""The generator: tools from connectors/github, and load-time rules a bad connector breaks."""

import shutil
from pathlib import Path

import pytest
import yaml
from gateway_testing import CONTRACTS

from gateway.config import CONNECTORS_FOLDER
from gateway.tools.openapi_to_mcp import ConnectorError, load_connector, load_hooks

GITHUB = CONNECTORS_FOLDER / "github"
ENV = {"GITHUB_REPOS": "octocat/Hello-World"}


def github(env=ENV, hooks=None):
    return load_connector(GITHUB, CONTRACTS, env, hooks)


def test_github_tools_expose_only_listed_arguments_under_github_names():
    tools = github().tools

    assert set(tools) == {"github.search_issues", "github.add_label", "github.close_issue"}
    search = tools["github.search_issues"].input_schema
    assert set(search["properties"]) == {"q"}  # sort, page, per_page ... do not exist
    assert search["required"] == ["q"]
    assert search["properties"]["q"]["maxLength"] == 256
    assert search["additionalProperties"] is False

    label = tools["github.add_label"].input_schema
    assert set(label["properties"]) == {"owner", "repo", "issue_number", "labels"}
    assert label["required"] == ["owner", "repo", "issue_number"]
    assert label["properties"]["labels"]["maxItems"] == 1


def test_close_narrows_the_reason_and_hides_state():
    close = github().tools["github.close_issue"]

    props = close.input_schema["properties"]
    assert props["state_reason"]["enum"] == ["completed", "not_planned", "duplicate"]
    assert "state" not in props and "title" not in props and "body" not in props
    assert close.fixed == {"state": "closed"}
    assert close.destructive and close.idempotent and not close.read_only


def test_schemas_are_plain_json_schema():
    for tool in github().tools.values():
        text = yaml.safe_dump(tool.input_schema)
        assert "$ref" not in text and "nullable" not in text and "example" not in text


def test_operations_are_the_tools_and_hook_operations_only():
    connector = github()

    assert set(connector.operations) == {
        "search/issues-and-pull-requests",
        "issues/add-labels",
        "issues/update",
        "issues/list-labels-for-repo",
        "issues/get",
    }
    assert connector.headers["X-GitHub-Api-Version"] == "2022-11-28"
    assert connector.settings == {"GITHUB_REPOS": ["octocat/Hello-World"]}


def test_description_defaults_to_the_operation_summary(tmp_path):
    folder = copy_github(tmp_path, lambda a: a["tools"]["github.add_label"].pop("description"))

    tool = load_connector(folder, CONTRACTS, ENV).tools["github.add_label"]
    assert tool.description == "Add labels to an issue"


def copy_github(tmp_path: Path, change) -> Path:
    folder = tmp_path / "github"
    shutil.copytree(GITHUB, folder)
    allowlist = yaml.safe_load((folder / "allowlist.yaml").read_text())
    change(allowlist)
    (folder / "allowlist.yaml").write_text(yaml.safe_dump(allowlist))
    return folder


def _tool(name, change):
    return lambda a: change(a["tools"][name])


BREAKS = {
    "tool without connector prefix": (
        lambda a: a["tools"].__setitem__("gitlab.search", a["tools"].pop("github.search_issues")),
        "must start with 'github.'",
    ),
    "unknown operation": (
        _tool("github.add_label", lambda t: t.update(operation="issues/delete")),
        "not in openapi.yaml",
    ),
    "unknown argument": (
        _tool("github.add_label", lambda t: t["arguments"].update(milestone={})),
        "not a parameter",
    ),
    "argument listed and fixed": (
        _tool("github.close_issue", lambda t: t["fixed"].update(state_reason="completed")),
        "both listed and fixed",
    ),
    "check on an unknown argument": (
        _tool("github.close_issue", lambda t: t["checks"][0].update(value="{org}/{repo}")),
        "unknown argument {org}",
    ),
    "enum wider than the API": (
        _tool(
            "github.close_issue",
            lambda t: t["arguments"]["state_reason"].update(enum=["completed", "spam"]),
        ),
        "does not accept",
    ),
    "unknown hook": (_tool("github.close_issue", lambda t: t.update(hook="nope")), "not defined"),
    "hook argument that is an API parameter": (
        _tool(
            "github.close_issue",
            lambda t: t.update(hook="h", hook_arguments={"title": {"type": "string"}}),
        ),
        "is an API parameter",
    ),
    "unknown hook operation": (
        lambda a: a["hook_operations"].append("repos/delete"),
        "hook_operations not in openapi.yaml",
    ),
    "bad refuse pattern": (
        _tool("github.search_issues", lambda t: t["arguments"]["q"].update(refuse_pattern="(")),
        "bad refuse_pattern",
    ),
    "allowlist breaking the contract": (
        _tool("github.add_label", lambda t: t.pop("annotations")),
        "allowlist.yaml",
    ),
}


@pytest.mark.parametrize(("change", "message"), BREAKS.values(), ids=BREAKS.keys())
def test_bad_connector_fails_at_load(tmp_path, change, message):
    folder = copy_github(tmp_path, change)

    with pytest.raises(ConnectorError, match=message):
        load_connector(folder, CONTRACTS, ENV, load_hooks(folder) | {"h": lambda call: None})


def test_missing_setting_fails_at_load():
    with pytest.raises(ConnectorError, match="GITHUB_REPOS is not set"):
        github(env={})
