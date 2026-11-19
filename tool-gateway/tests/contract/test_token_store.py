"""TokenStore contract: every adapter passes (the SSM adapter joins ADAPTERS in M6)."""

import pytest

from gateway.adapters.token_store_memory import MemoryTokenStore
from gateway.ports.token_store import CredentialKey

ADAPTERS = [pytest.param(MemoryTokenStore, id="memory")]
BOT = CredentialKey("tenant-a", "team-bot", "github")


@pytest.fixture(params=ADAPTERS)
def store(request):
    return request.param()


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


def test_delete(store):
    store.put(BOT, {"v": 1})

    assert store.delete(BOT) is True
    assert store.get(BOT) is None
    assert store.delete(BOT) is False
