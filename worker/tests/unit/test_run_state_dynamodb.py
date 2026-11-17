"""DynamoDB adapter errors that DynamoDB Local cannot produce, via botocore's Stubber."""

import boto3
import pytest
from botocore.stub import Stubber

from worker.adapters.run_state_dynamodb import DynamoDbRunStateStore
from worker.core.errors import StateError


def stubbed(clock):
    client = boto3.client(
        "dynamodb",
        region_name="us-east-1",
        aws_access_key_id="local",
        aws_secret_access_key="local",
    )
    return DynamoDbRunStateStore("run-state", clock, client=client), Stubber(client)


def test_throttling_after_retries_becomes_state_error(clock):
    store, stub = stubbed(clock)
    stub.add_client_error("update_item", "ProvisionedThroughputExceededException", "Rate exceeded")

    with stub, pytest.raises(StateError, match="ProvisionedThroughputExceededException"):
        store.take_lease("run_1", "worker-a", 30)


def test_racing_transaction_becomes_state_error_not_conflict(clock):
    store, stub = stubbed(clock)
    stub.add_client_error(
        "transact_write_items",
        "TransactionCanceledException",
        "Transaction cancelled",
        modeled_fields={"CancellationReasons": [{"Code": "TransactionConflict"}, {"Code": "None"}]},
    )

    with stub, pytest.raises(StateError, match="TransactionCanceledException"):
        store.save_checkpoint("run_1", "worker-a", 1, {"n": 1})
