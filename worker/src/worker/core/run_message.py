"""Parses the run queue message body (contracts/run-message.schema.json).

Hand-written checks instead of loading the schema file: the deployed worker image does not ship
contracts/. tests/unit/test_run_message.py checks this parser against the contract's examples.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

SCHEMA_VERSION = 1
_FIELDS = {"schema_version", "run_id", "tenant_id"}
_MAX_ID = 64


class InvalidRunMessage(Exception):
    """The message body does not match the run message contract."""


@dataclass(frozen=True)
class RunMessage:
    run_id: str
    tenant_id: str


def parse_run_message(body: str) -> RunMessage:
    try:
        data = json.loads(body)
    except ValueError as e:
        raise InvalidRunMessage(f"not JSON: {e}") from None
    if not isinstance(data, dict):
        raise InvalidRunMessage("not a JSON object")
    if missing := _FIELDS - data.keys():
        raise InvalidRunMessage(f"missing fields: {', '.join(sorted(missing))}")
    if extra := data.keys() - _FIELDS:
        raise InvalidRunMessage(f"unknown fields: {', '.join(sorted(extra))}")
    if data["schema_version"] != SCHEMA_VERSION or isinstance(data["schema_version"], bool):
        raise InvalidRunMessage(f"unsupported schema_version: {data['schema_version']!r}")
    for name in ("run_id", "tenant_id"):
        value = data[name]
        if not isinstance(value, str) or not 1 <= len(value) <= _MAX_ID:
            raise InvalidRunMessage(f"{name} must be a string of 1 to {_MAX_ID} characters")
    return RunMessage(run_id=data["run_id"], tenant_id=data["tenant_id"])
