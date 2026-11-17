"""RunStateStore on Amazon DynamoDB (DynamoDB Local in tests, via AWS_ENDPOINT_URL_DYNAMODB).

Table: worker/dynamodb/run-state-table.json (keys), run-state-ttl.json (TTL on `ttl`). One
partition per run:

| SK            | Attributes                                   | Written by                    |
| ------------- | -------------------------------------------- | ----------------------------- |
| `LEASE`       | `owner`, `expires_at`, `last_step`, `ttl`    | every lease change, each step |
| `STEP#000001` | `data` (JSON of what the step added), `ttl`  | save_checkpoint, never changed |
| `TOOL#<key>`  | `result` (JSON, once stored), `ttl`          | claim / complete_tool_call    |

Fencing: every write other than take_lease is a transaction whose first part checks the caller's
live lease on the `LEASE` item, so a worker that lost its lease changes nothing. A checkpoint also
moves `last_step` from n-1 to n in that same check, which is what makes "step n only after n-1"
atomic. Retried transactions are safe: botocore sends the same ClientRequestToken on every retry
of one call.

`ttl` (epoch seconds) is set to now + retention on every write; DynamoDB deletes expired items in
the background, usually within days. Items of a run whose last write is older than the retention
are gone, so a run must finish (or be abandoned) within it.
"""

from __future__ import annotations

import json
from typing import Any

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

from worker.adapters.run_state_payload import encode
from worker.core.errors import CheckpointConflict, LeaseLost, StateError
from worker.ports.clock import Clock
from worker.ports.run_state import Checkpoint, Lease, ToolClaim

RETENTION_SECONDS = 15 * 24 * 3600
LEASE_SK = "LEASE"
STEP_DIGITS = 6
# Attribute names go through placeholders: `owner` and `ttl` are DynamoDB reserved words.
NAMES = {"#o": "owner", "#e": "expires_at", "#s": "last_step", "#t": "ttl"}
LIVE = "#o = :me AND #e > :now"


def step_sk(step: int) -> str:
    return f"STEP#{step:0{STEP_DIGITS}d}"


def tool_sk(key: str) -> str:
    return f"TOOL#{key}"


class DynamoDbRunStateStore:
    def __init__(
        self,
        table: str,
        clock: Clock,
        client: Any = None,
        region: str | None = None,
        retention_seconds: int = RETENTION_SECONDS,
        attempts: int = 4,
    ) -> None:
        self._table = table
        self._clock = clock
        self._retention = retention_seconds
        # boto3 reads AWS_DEFAULT_REGION but not AWS_REGION, so the region is passed explicitly.
        self._db = client or boto3.client(
            "dynamodb",
            region_name=region,
            config=BotoConfig(retries={"mode": "standard", "max_attempts": attempts}),
        )

    def take_lease(self, run_id: str, owner: str, seconds: float) -> Lease | None:
        now = self._clock.time()
        try:
            item = self._db.update_item(
                TableName=self._table,
                Key=_key(run_id, LEASE_SK),
                UpdateExpression="SET #o = :me, #e = :exp, #t = :ttl, "
                "#s = if_not_exists(#s, :zero)",
                ConditionExpression="attribute_not_exists(PK) OR #o = :me OR #e <= :now",
                ExpressionAttributeNames=NAMES,
                ExpressionAttributeValues={
                    ":me": {"S": owner},
                    ":now": _n(now),
                    ":exp": _n(now + seconds),
                    ":ttl": _n(self._ttl(now)),
                    ":zero": _n(0),
                },
                ReturnValues="ALL_NEW",
            )["Attributes"]
        except ClientError as e:
            if _code(e) == "ConditionalCheckFailedException":
                return None
            raise _state_error("take lease", e) from e
        except BotoCoreError as e:
            raise _state_error("take lease", e) from e
        return _lease(run_id, item)

    def renew_lease(self, run_id: str, owner: str, seconds: float) -> Lease:
        now = self._clock.time()
        try:
            item = self._db.update_item(
                TableName=self._table,
                Key=_key(run_id, LEASE_SK),
                UpdateExpression="SET #e = :exp, #t = :ttl",
                ConditionExpression=LIVE,
                ExpressionAttributeNames={k: v for k, v in NAMES.items() if k != "#s"},
                ExpressionAttributeValues={
                    ":me": {"S": owner},
                    ":now": _n(now),
                    ":exp": _n(now + seconds),
                    ":ttl": _n(self._ttl(now)),
                },
                ReturnValues="ALL_NEW",
            )["Attributes"]
        except ClientError as e:
            if _code(e) == "ConditionalCheckFailedException":
                raise _lost(run_id, owner) from e
            raise _state_error("renew lease", e) from e
        except BotoCoreError as e:
            raise _state_error("renew lease", e) from e
        return _lease(run_id, item)

    def release_lease(self, run_id: str, owner: str) -> None:
        try:
            self._db.update_item(
                TableName=self._table,
                Key=_key(run_id, LEASE_SK),
                UpdateExpression="SET #e = :zero",
                ConditionExpression="#o = :me",
                ExpressionAttributeNames={"#o": "owner", "#e": "expires_at"},
                ExpressionAttributeValues={":me": {"S": owner}, ":zero": _n(0)},
            )
        except ClientError as e:
            if _code(e) != "ConditionalCheckFailedException":  # not ours: nothing to release
                raise _state_error("release lease", e) from e
        except BotoCoreError as e:
            raise _state_error("release lease", e) from e

    def save_checkpoint(self, run_id: str, owner: str, step: int, data: dict[str, Any]) -> None:
        payload = encode(data, f"Checkpoint {step}")
        now = self._clock.time()
        ttl = _n(self._ttl(now))
        lease_part = {
            "Update": {
                "TableName": self._table,
                "Key": _key(run_id, LEASE_SK),
                "UpdateExpression": "SET #s = :step, #t = :ttl",
                "ConditionExpression": f"{LIVE} AND #s = :prev",
                "ExpressionAttributeNames": NAMES,
                "ExpressionAttributeValues": {
                    ":me": {"S": owner},
                    ":now": _n(now),
                    ":step": _n(step),
                    ":prev": _n(step - 1),
                    ":ttl": ttl,
                },
                "ReturnValuesOnConditionCheckFailure": "ALL_OLD",
            }
        }
        step_part = {
            "Put": {
                "TableName": self._table,
                "Item": {**_key(run_id, step_sk(step)), "data": {"S": payload}, "ttl": ttl},
                "ConditionExpression": "attribute_not_exists(PK)",
            }
        }
        reasons = self._transact("save checkpoint", [lease_part, step_part])
        if reasons is None:
            return
        lease_reason, _ = reasons
        if lease_reason.get("Code") == "ConditionalCheckFailed":
            old = lease_reason.get("Item")
            if not _is_live(old, owner, now):
                raise _lost(run_id, owner)
            last = int(old["last_step"]["N"]) if "last_step" in old else 0
            raise CheckpointConflict(f"Run {run_id}: step {step} is not next (last saved {last})")
        raise CheckpointConflict(f"Run {run_id}: step {step} is already saved")

    def load_checkpoints(self, run_id: str) -> list[Checkpoint]:
        checkpoints: list[Checkpoint] = []
        request: dict[str, Any] = {
            "TableName": self._table,
            "KeyConditionExpression": "PK = :pk AND begins_with(SK, :step)",
            "ExpressionAttributeValues": {":pk": {"S": run_id}, ":step": {"S": "STEP#"}},
            "ConsistentRead": True,
        }
        try:
            while True:
                page = self._db.query(**request)
                for item in page.get("Items", []):
                    step = int(item["SK"]["S"].removeprefix("STEP#"))
                    checkpoints.append(Checkpoint(step, json.loads(item["data"]["S"])))
                if "LastEvaluatedKey" not in page:
                    break
                request["ExclusiveStartKey"] = page["LastEvaluatedKey"]
        except (BotoCoreError, ClientError) as e:
            raise _state_error("load checkpoints", e) from e
        return checkpoints  # sorted by SK, which zero-padding makes step order

    def claim_tool_call(self, run_id: str, owner: str, key: str) -> ToolClaim:
        now = self._clock.time()
        claim = {
            "Put": {
                "TableName": self._table,
                "Item": {**_key(run_id, tool_sk(key)), "ttl": _n(self._ttl(now))},
                "ConditionExpression": "attribute_not_exists(PK)",
            }
        }
        reasons = self._transact("claim tool call", [self._lease_check(run_id, owner, now), claim])
        if reasons is None:
            return ToolClaim(fresh=True)
        if reasons[0].get("Code") == "ConditionalCheckFailed":
            raise _lost(run_id, owner)
        try:
            item = self._db.get_item(
                TableName=self._table, Key=_key(run_id, tool_sk(key)), ConsistentRead=True
            ).get("Item", {})
        except (BotoCoreError, ClientError) as e:
            raise _state_error("read tool call", e) from e
        stored = item.get("result")
        return ToolClaim(fresh=False, result=None if stored is None else json.loads(stored["S"]))

    def complete_tool_call(self, run_id: str, owner: str, key: str, result: dict[str, Any]) -> None:
        payload = encode(result, f"Tool result {key}")
        now = self._clock.time()
        store = {
            "Update": {
                "TableName": self._table,
                "Key": _key(run_id, tool_sk(key)),
                "UpdateExpression": "SET #r = :result, #t = :ttl",
                "ConditionExpression": "attribute_exists(PK) AND attribute_not_exists(#r)",
                "ExpressionAttributeNames": {"#r": "result", "#t": "ttl"},
                "ExpressionAttributeValues": {
                    ":result": {"S": payload},
                    ":ttl": _n(self._ttl(now)),
                },
            }
        }
        reasons = self._transact(
            "complete tool call", [self._lease_check(run_id, owner, now), store]
        )
        if reasons is None:
            return
        if reasons[0].get("Code") == "ConditionalCheckFailed":
            raise _lost(run_id, owner)
        raise StateError(f"Run {run_id}: tool key {key} is not claimed without a result")

    def _lease_check(self, run_id: str, owner: str, now: float) -> dict[str, Any]:
        return {
            "ConditionCheck": {
                "TableName": self._table,
                "Key": _key(run_id, LEASE_SK),
                "ConditionExpression": LIVE,
                "ExpressionAttributeNames": {"#o": "owner", "#e": "expires_at"},
                "ExpressionAttributeValues": {":me": {"S": owner}, ":now": _n(now)},
            }
        }

    def _transact(self, what: str, parts: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
        """None on success; the per-part cancellation reasons if a condition failed."""
        try:
            self._db.transact_write_items(TransactItems=parts)
            return None
        except ClientError as e:
            reasons = e.response.get("CancellationReasons") or []
            codes = {r.get("Code") for r in reasons}
            if _code(e) == "TransactionCanceledException" and "ConditionalCheckFailed" in codes:
                return reasons
            raise _state_error(what, e) from e  # includes TransactionConflict (a racing write)
        except BotoCoreError as e:
            raise _state_error(what, e) from e

    def _ttl(self, now: float) -> int:
        return int(now) + self._retention


def _key(run_id: str, sk: str) -> dict[str, Any]:
    return {"PK": {"S": run_id}, "SK": {"S": sk}}


def _n(value: float) -> dict[str, str]:
    return {"N": repr(value) if isinstance(value, float) else str(value)}


def _code(e: ClientError) -> str:
    return e.response.get("Error", {}).get("Code", "")


def _lease(run_id: str, item: dict[str, Any]) -> Lease:
    return Lease(
        run_id=run_id,
        owner=item["owner"]["S"],
        expires_at=float(item["expires_at"]["N"]),
        last_step=int(item["last_step"]["N"]),
    )


def _is_live(item: dict[str, Any] | None, owner: str, now: float) -> bool:
    return (
        item is not None
        and item.get("owner", {}).get("S") == owner
        and float(item.get("expires_at", {}).get("N", "0")) > now
    )


def _lost(run_id: str, owner: str) -> LeaseLost:
    return LeaseLost(f"Run {run_id}: {owner} does not hold a live lease")


def _state_error(what: str, e: Exception) -> StateError:
    if isinstance(e, ClientError):
        error = e.response.get("Error", {})
        return StateError(f"DynamoDB {what} failed: {error.get('Code')}: {error.get('Message')}")
    return StateError(f"DynamoDB {what} failed: {type(e).__name__}: {e}")
