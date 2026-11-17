"""The hand-written run message parser agrees with contracts/run-message.schema.json."""

import contextlib
import json

import pytest
from contracts import CONTRACTS
from jsonschema import Draft202012Validator

from worker.core.run_message import InvalidRunMessage, RunMessage, parse_run_message

EXAMPLES = CONTRACTS / "examples"
SCHEMA = Draft202012Validator(json.loads((CONTRACTS / "run-message.schema.json").read_text()))


def test_parses_contract_valid_example():
    body = (EXAMPLES / "run-message.valid.json").read_text()

    message = parse_run_message(body)

    expected = json.loads(body)
    assert message == RunMessage(expected["run_id"], expected["tenant_id"])


def test_rejects_contract_invalid_example():
    with pytest.raises(InvalidRunMessage):
        parse_run_message((EXAMPLES / "run-message.invalid.json").read_text())


@pytest.mark.parametrize(
    "body",
    [
        "not json",
        "[]",
        '{"schema_version": 2, "run_id": "r", "tenant_id": "t"}',
        '{"schema_version": true, "run_id": "r", "tenant_id": "t"}',
        '{"schema_version": 1, "run_id": "", "tenant_id": "t"}',
        '{"schema_version": 1, "run_id": 5, "tenant_id": "t"}',
        '{"schema_version": 1, "run_id": "' + "x" * 65 + '", "tenant_id": "t"}',
        '{"schema_version": 1, "run_id": "r", "tenant_id": "t", "extra": 1}',
    ],
)
def test_rejects_bodies_the_schema_rejects(body):
    with contextlib.suppress(ValueError):  # "not json" has nothing for the schema to check
        assert not SCHEMA.is_valid(json.loads(body)), "the contract accepts this body"
    with pytest.raises(InvalidRunMessage):
        parse_run_message(body)
