"""Port: stored credentials of the gateway (architecture → Ports and adapters).

A credential belongs to a tenant, an identity (`team-bot`, or a principal such as `user:42`
for acts_as owner) and a provider (`github`, ...). Values are small JSON objects (an OAuth token
pair, a GitHub App id and private key). Adapters encrypt at rest where they can (SSM SecureString
in M6); the in-memory one does not. Every adapter passes tests/contract/test_token_store.py.
"""

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class CredentialKey:
    tenant: str
    identity: str
    provider: str


class TokenStore(Protocol):
    def get(self, key: CredentialKey) -> dict[str, Any] | None:
        """The stored value, or None."""
        ...

    def put(self, key: CredentialKey, value: dict[str, Any]) -> None:
        """Stores or replaces the value."""
        ...

    def delete(self, key: CredentialKey) -> bool:
        """Removes the value; False if there was none."""
        ...
