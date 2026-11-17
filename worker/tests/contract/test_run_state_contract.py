"""Behavior every RunStateStore adapter must have. DynamoDB runs against DynamoDB Local in Docker
(the same image as the local stack), started by Testcontainers; skip it with `-m "not docker"`.
Each DynamoDB test gets its own table, created from worker/dynamodb/*.json.

Time comes from the shared FakeClock, which both adapters read for lease expiry, so expiry is
tested by moving the clock, not by sleeping.
"""

import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from worker.adapters.run_state_dynamodb import RETENTION_SECONDS, DynamoDbRunStateStore
from worker.adapters.run_state_memory import InMemoryRunStateStore
from worker.core.errors import CheckpointConflict, LeaseLost, StateError
from worker.ports.run_state import MAX_ITEM_BYTES, Checkpoint, ToolClaim

DYNAMODB_IMAGE = "amazon/dynamodb-local:3.3.2"  # keep in step with docker-compose.yml
TABLE_FILES = Path(__file__).resolve().parents[2] / "dynamodb"
RUN = "run_0f6c1d2e"
A, B = "worker-a", "worker-b"
LEASE = 30


@pytest.fixture(scope="session")
def dynamodb_client():
    import boto3
    from testcontainers.core.container import DockerContainer
    from testcontainers.core.wait_strategies import LogMessageWaitStrategy

    container = (
        DockerContainer(DYNAMODB_IMAGE)
        .with_command("-jar DynamoDBLocal.jar -inMemory -sharedDb")
        .with_exposed_ports(8000)
        .waiting_for(LogMessageWaitStrategy("CorsParams"))
    )
    with container:
        endpoint = f"http://{container.get_container_host_ip()}:{container.get_exposed_port(8000)}"
        client = boto3.client(
            "dynamodb",
            endpoint_url=endpoint,
            region_name="us-east-1",
            aws_access_key_id="local",
            aws_secret_access_key="local",
        )
        for _ in range(50):  # the log line can come just before the port answers
            try:
                client.list_tables()
                break
            except Exception:
                time.sleep(0.2)
        yield client


def create_table(client) -> str:
    """A fresh table from the definition files; returns its name."""
    name = f"run-state-{uuid.uuid4().hex[:8]}"
    table = json.loads((TABLE_FILES / "run-state-table.json").read_text())
    client.create_table(**{**table, "TableName": name})
    ttl = json.loads((TABLE_FILES / "run-state-ttl.json").read_text())
    client.update_time_to_live(**{**ttl, "TableName": name})
    return name


def _memory(request, clock):
    return InMemoryRunStateStore(clock)


def _dynamodb(request, clock):
    client = request.getfixturevalue("dynamodb_client")
    return DynamoDbRunStateStore(create_table(client), clock, client=client)


ADAPTERS = [
    pytest.param(_memory, id="memory"),
    pytest.param(_dynamodb, id="dynamodb", marks=pytest.mark.docker),
]


@pytest.fixture(params=ADAPTERS)
def store(request, clock):
    return request.param(request, clock)


# Lease


def test_first_lease_starts_at_step_zero(store, clock):
    lease = store.take_lease(RUN, A, LEASE)

    assert lease.owner == A
    assert lease.last_step == 0
    assert lease.expires_at == pytest.approx(clock.time() + LEASE)


def test_live_lease_refuses_another_worker(store):
    store.take_lease(RUN, A, LEASE)

    assert store.take_lease(RUN, B, LEASE) is None


def test_holder_can_take_its_lease_again(store, clock):
    store.take_lease(RUN, A, LEASE)
    clock.advance(10)

    lease = store.take_lease(RUN, A, LEASE)

    assert lease.expires_at == pytest.approx(clock.time() + LEASE)


def test_expired_lease_can_be_taken_over_and_keeps_progress(store, clock):
    store.take_lease(RUN, A, LEASE)
    store.save_checkpoint(RUN, A, 1, {"n": 1})
    clock.advance(LEASE + 1)

    lease = store.take_lease(RUN, B, LEASE)

    assert lease.owner == B
    assert lease.last_step == 1


def test_renew_extends_the_lease(store, clock):
    store.take_lease(RUN, A, LEASE)
    clock.advance(LEASE - 1)

    store.renew_lease(RUN, A, LEASE)
    clock.advance(LEASE - 1)

    assert store.take_lease(RUN, B, LEASE) is None


def test_renew_fails_for_a_worker_without_the_lease(store, clock):
    store.take_lease(RUN, A, LEASE)

    with pytest.raises(LeaseLost):
        store.renew_lease(RUN, B, LEASE)
    clock.advance(LEASE + 1)
    with pytest.raises(LeaseLost):
        store.renew_lease(RUN, A, LEASE)
    with pytest.raises(LeaseLost):
        store.renew_lease("run_unknown", A, LEASE)


def test_release_lets_another_worker_take_the_run_at_once(store):
    store.take_lease(RUN, A, LEASE)

    store.release_lease(RUN, A)

    assert store.take_lease(RUN, B, LEASE).owner == B


def test_release_by_a_non_holder_changes_nothing(store):
    store.take_lease(RUN, A, LEASE)

    store.release_lease(RUN, B)
    store.release_lease("run_unknown", B)

    assert store.take_lease(RUN, B, LEASE) is None


def test_concurrent_takers_exactly_one_wins(store):
    owners = [f"worker-{i}" for i in range(8)]
    start = threading.Barrier(len(owners))

    def take(owner):
        start.wait()
        return store.take_lease(RUN, owner, LEASE)

    with ThreadPoolExecutor(len(owners)) as pool:
        leases = list(pool.map(take, owners))

    assert len([lease for lease in leases if lease is not None]) == 1


# Checkpoints


def test_checkpoints_load_in_step_order(store):
    store.take_lease(RUN, A, LEASE)
    for step in range(1, 13):  # past 9 and 10: order must not be by text
        store.save_checkpoint(RUN, A, step, {"step": step, "text": "état ✓"})

    loaded = store.load_checkpoints(RUN)

    assert [c.step for c in loaded] == list(range(1, 13))
    assert loaded[0] == Checkpoint(1, {"step": 1, "text": "état ✓"})


def test_unknown_run_has_no_checkpoints(store):
    assert store.load_checkpoints("run_unknown") == []


def test_checkpoint_must_be_the_next_step(store):
    store.take_lease(RUN, A, LEASE)
    store.save_checkpoint(RUN, A, 1, {"n": 1})

    with pytest.raises(CheckpointConflict):
        store.save_checkpoint(RUN, A, 3, {"n": 3})
    with pytest.raises(CheckpointConflict):
        store.save_checkpoint(RUN, A, 1, {"n": "again"})

    assert store.load_checkpoints(RUN) == [Checkpoint(1, {"n": 1})]
    assert store.take_lease(RUN, A, LEASE).last_step == 1


def test_checkpoint_needs_a_live_lease(store, clock):
    with pytest.raises(LeaseLost):
        store.save_checkpoint(RUN, A, 1, {"n": 1})
    store.take_lease(RUN, A, LEASE)
    clock.advance(LEASE + 1)
    with pytest.raises(LeaseLost):
        store.save_checkpoint(RUN, A, 1, {"n": 1})

    assert store.load_checkpoints(RUN) == []


def test_worker_that_lost_its_lease_cannot_overwrite_the_new_holder(store, clock):
    store.take_lease(RUN, A, LEASE)
    store.save_checkpoint(RUN, A, 1, {"by": A})
    clock.advance(LEASE + 1)
    store.take_lease(RUN, B, LEASE)
    store.save_checkpoint(RUN, B, 2, {"by": B})

    with pytest.raises(LeaseLost):
        store.save_checkpoint(RUN, A, 2, {"by": A})
    with pytest.raises(LeaseLost):
        store.save_checkpoint(RUN, A, 3, {"by": A})

    assert [c.data["by"] for c in store.load_checkpoints(RUN)] == [A, B]


def test_concurrent_saves_of_one_step_keep_one(store):
    store.take_lease(RUN, A, LEASE)
    start = threading.Barrier(4)

    def save(i):
        start.wait()
        try:
            store.save_checkpoint(RUN, A, 1, {"i": i})
            return True
        except (CheckpointConflict, StateError):  # StateError: DynamoDB TransactionConflict
            return False

    with ThreadPoolExecutor(4) as pool:
        saved = list(pool.map(save, range(4)))

    assert saved.count(True) == 1
    assert len(store.load_checkpoints(RUN)) == 1


def test_oversized_or_unserializable_payload_is_refused(store):
    store.take_lease(RUN, A, LEASE)

    with pytest.raises(StateError, match="limit"):
        store.save_checkpoint(RUN, A, 1, {"text": "x" * MAX_ITEM_BYTES})
    with pytest.raises(StateError, match="JSON"):
        store.save_checkpoint(RUN, A, 1, {"value": object()})

    assert store.load_checkpoints(RUN) == []
    store.save_checkpoint(RUN, A, 1, {"ok": True})  # step 1 is still free


# Tool-call idempotency


def test_first_claim_is_fresh_and_a_second_sees_no_result(store):
    store.take_lease(RUN, A, LEASE)

    assert store.claim_tool_call(RUN, A, "k1") == ToolClaim(fresh=True)
    assert store.claim_tool_call(RUN, A, "k1") == ToolClaim(fresh=False, result=None)


def test_stored_result_is_returned_to_later_claims(store, clock):
    store.take_lease(RUN, A, LEASE)
    store.claim_tool_call(RUN, A, "k1")
    store.complete_tool_call(RUN, A, "k1", {"content": "labeled", "is_error": False})
    clock.advance(LEASE + 1)
    store.take_lease(RUN, B, LEASE)

    claim = store.claim_tool_call(RUN, B, "k1")

    assert claim == ToolClaim(fresh=False, result={"content": "labeled", "is_error": False})


def test_result_is_stored_once_and_only_for_a_claimed_key(store):
    store.take_lease(RUN, A, LEASE)
    store.claim_tool_call(RUN, A, "k1")
    store.complete_tool_call(RUN, A, "k1", {"content": "first"})

    with pytest.raises(StateError):
        store.complete_tool_call(RUN, A, "k1", {"content": "second"})
    with pytest.raises(StateError):
        store.complete_tool_call(RUN, A, "never-claimed", {"content": "x"})

    assert store.claim_tool_call(RUN, A, "k1").result == {"content": "first"}


def test_tool_calls_need_a_live_lease(store, clock):
    with pytest.raises(LeaseLost):
        store.claim_tool_call(RUN, A, "k1")
    store.take_lease(RUN, A, LEASE)
    store.claim_tool_call(RUN, A, "k1")
    clock.advance(LEASE + 1)

    with pytest.raises(LeaseLost):
        store.claim_tool_call(RUN, A, "k2")
    with pytest.raises(LeaseLost):
        store.complete_tool_call(RUN, A, "k1", {"content": "late"})


def test_runs_are_independent(store):
    store.take_lease(RUN, A, LEASE)
    store.take_lease("run_other", B, LEASE)
    store.save_checkpoint(RUN, A, 1, {"run": 1})
    store.claim_tool_call(RUN, A, "k1")

    assert store.load_checkpoints("run_other") == []
    assert store.claim_tool_call("run_other", B, "k1").fresh


# DynamoDB only


@pytest.mark.docker
def test_dynamodb_items_expire_after_retention(dynamodb_client, clock):
    table = create_table(dynamodb_client)
    store = DynamoDbRunStateStore(table, clock, client=dynamodb_client)
    store.take_lease(RUN, A, LEASE)
    store.save_checkpoint(RUN, A, 1, {"n": 1})
    store.claim_tool_call(RUN, A, "k1")

    items = dynamodb_client.query(
        TableName=table,
        KeyConditionExpression="PK = :pk",
        ExpressionAttributeValues={":pk": {"S": RUN}},
    )["Items"]

    assert {i["SK"]["S"] for i in items} == {"LEASE", "STEP#000001", "TOOL#k1"}
    expected = int(clock.time()) + RETENTION_SECONDS
    assert all(int(i["ttl"]["N"]) == expected for i in items)
    ttl = dynamodb_client.describe_time_to_live(TableName=table)["TimeToLiveDescription"]
    assert ttl["AttributeName"] == "ttl"
