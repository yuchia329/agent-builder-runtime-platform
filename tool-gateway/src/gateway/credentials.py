"""Which credential a call uses (ADR 0003): the connector's kind for the run's acts_as, looked up in
the TokenStore under (tenant, identity, connector), identity being `team-bot` or the run's owner.

Kinds (`credentials` in the allowlist):
- `github_app`: {"app_id", "private_key", "installation_id"}, exchanged for an installation token
  (gateway.github_app); renewable once when GitHub answers 401.
- `bot_token`, and `oauth` until M4 task 10: a stored {"token": "..."}.

Errors name what is missing, never a value.
"""

from collections.abc import Callable

from gateway.auth.run_token import RunClaims
from gateway.github_app import AppCredential, GitHubAppTokens
from gateway.ports.token_store import CredentialKey, TokenStore
from gateway.tools.registry import ToolFailure


class Auth:
    """Headers for one call; `renew()` swaps in a fresh credential if the kind allows it."""

    def __init__(
        self,
        headers: dict[str, str],
        renewer: Callable[[], dict[str, str]] | None = None,
    ) -> None:
        self.headers = headers
        self._renewer = renewer

    def renew(self) -> bool:
        if self._renewer is None:
            return False
        self.headers = self._renewer()
        return True


class Credentials:
    def __init__(self, store: TokenStore, github_app: GitHubAppTokens | None = None) -> None:
        self._store = store
        self._github_app = github_app or GitHubAppTokens()

    def auth(self, connector: str, kinds: dict[str, str], claims: RunClaims, base_url: str) -> Auth:
        if claims.acts_as not in kinds:
            raise ToolFailure(f"The {connector} connector cannot act as {claims.acts_as}")
        kind = kinds[claims.acts_as]
        identity = "team-bot" if claims.acts_as == "team-bot" else claims.owner
        value = self._store.get(CredentialKey(claims.tenant, identity, connector))
        if not value:
            raise ToolFailure(
                f"No {connector} credential for {identity} in tenant {claims.tenant}; "
                "ask an operator to connect it"
            )
        if kind == "github_app":
            app = AppCredential.from_value(value)

            def bearer(renew: bool) -> dict[str, str]:
                return {"Authorization": f"Bearer {self._github_app.token(app, base_url, renew)}"}

            return Auth(bearer(False), lambda: bearer(True))
        if not value.get("token"):
            raise ToolFailure(
                f"The {connector} credential for {identity} has no token; "
                "ask an operator to connect it"
            )
        return Auth({"Authorization": f"Bearer {value['token']}"})
