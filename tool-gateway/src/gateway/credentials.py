"""Which credential a call uses (ADR 0003): the connector's kind for the run's acts_as, looked up in
the TokenStore under (tenant, identity, connector), identity being `team-bot` or the run's owner.

Until the GitHub App and OAuth are built (M4 tasks 6 and 10) a stored value is used as
{"token": "..."}; those tasks replace the per-kind lookup. Errors name what is missing, never a
value.
"""

from gateway.auth.run_token import RunClaims
from gateway.ports.token_store import CredentialKey, TokenStore
from gateway.tools.registry import ToolFailure


class Credentials:
    def __init__(self, store: TokenStore) -> None:
        self._store = store

    def headers(self, connector: str, kinds: dict[str, str], claims: RunClaims) -> dict[str, str]:
        if claims.acts_as not in kinds:
            raise ToolFailure(f"The {connector} connector cannot act as {claims.acts_as}")
        identity = "team-bot" if claims.acts_as == "team-bot" else claims.owner
        value = self._store.get(CredentialKey(claims.tenant, identity, connector))
        if not value or not value.get("token"):
            raise ToolFailure(
                f"No {connector} credential for {identity} in tenant {claims.tenant}; "
                "ask an operator to connect it"
            )
        return {"Authorization": f"Bearer {value['token']}"}
