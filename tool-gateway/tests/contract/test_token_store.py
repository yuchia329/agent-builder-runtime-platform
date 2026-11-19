"""TokenStore contract: every adapter passes. SSM runs against moto (AWS imitated in process)."""

import boto3
import pytest
from moto import mock_aws

from gateway.adapters.token_store_memory import MemoryTokenStore
from gateway.adapters.token_store_ssm import SsmTokenStore
from gateway.ports.token_store import CredentialKey


def memory():
    yield MemoryTokenStore()


def ssm():
    with mock_aws():
        yield SsmTokenStore(boto3.client("ssm", region_name="us-east-1"), "/test/gateway/tokens")


ADAPTERS = [pytest.param(memory, id="memory"), pytest.param(ssm, id="ssm")]
BOT = CredentialKey("tenant-a", "team-bot", "github")


@pytest.fixture(params=ADAPTERS)
def store(request, monkeypatch):
    for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        monkeypatch.setenv(name, "testing")  # moto never sends them anywhere
    yield from request.param()


def test_missing_is_none(store):
    assert store.get(BOT) is None


def test_put_then_get_returns_an_equal_copy(store):
    value = {"app_id": 123, "private_key": "-----BEGIN...", "nested": {"a": [1]}}
    store.put(BOT, value)

    got = store.get(BOT)
    assert got == value
    got["nested"]["a"].append(2)
    assert store.get(BOT) == value  # callers cannot change what is stored


def test_put_replaces(store):
    store.put(BOT, {"v": 1})
    store.put(BOT, {"v": 2})

    assert store.get(BOT) == {"v": 2}


def test_keys_are_isolated_by_tenant_identity_and_provider(store):
    store.put(BOT, {"v": "bot"})

    for other in (
        CredentialKey("tenant-b", "team-bot", "github"),
        CredentialKey("tenant-a", "user:42", "github"),
        CredentialKey("tenant-a", "team-bot", "slack"),
    ):
        assert store.get(other) is None


def test_identities_with_any_characters_are_kept_apart(store):
    # Principals look like api:tenant-a or user:42; escaping must never make two keys collide.
    keys = [
        CredentialKey("tenant-a", identity, "github")
        for identity in ("user:42", "user_3A42", "user/42", "user 42", "ü:1")
    ]
    for i, key in enumerate(keys):
        store.put(key, {"i": i})

    assert [store.get(key) for key in keys] == [{"i": i} for i in range(len(keys))]


def test_delete(store):
    store.put(BOT, {"v": 1})

    assert store.delete(BOT) is True
    assert store.get(BOT) is None
    assert store.delete(BOT) is False
