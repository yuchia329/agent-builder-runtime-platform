"""Local credentials from the environment, put in the in-memory TokenStore at startup.

Only for TOKEN_STORE=memory (local runs). On AWS an operator writes the TokenStore directly (M6).

GitHub App for acts_as team-bot, all or none of:
- TENANT_ID: the tenant the bot belongs to;
- GITHUB_APP_ID, GITHUB_APP_INSTALLATION_ID: numbers from the App's settings and installation;
- GITHUB_APP_PRIVATE_KEY_FILE: path to the App's .pem (kept outside the repo; read here, never
  logged).
"""

from pathlib import Path

from gateway.ports.token_store import CredentialKey, TokenStore

GITHUB_APP = (
    "TENANT_ID",
    "GITHUB_APP_ID",
    "GITHUB_APP_INSTALLATION_ID",
    "GITHUB_APP_PRIVATE_KEY_FILE",
)
APP_ONLY = GITHUB_APP[1:]


def seed_from_env(store: TokenStore, env: dict[str, str]) -> list[str]:
    """Stores what the environment configures; returns a description of each (no values)."""
    value = {name: env.get(name, "").strip() for name in GITHUB_APP}
    if not any(value[name] for name in APP_ONLY):
        return []
    missing = [name for name in GITHUB_APP if not value[name]]
    if missing:
        raise ValueError(f"GitHub App settings incomplete: set {', '.join(missing)}")
    path = Path(value["GITHUB_APP_PRIVATE_KEY_FILE"]).expanduser()
    try:
        private_key = path.read_text()
    except OSError:
        raise ValueError(f"GITHUB_APP_PRIVATE_KEY_FILE cannot be read: {path}") from None
    if "PRIVATE KEY-----" not in private_key:
        raise ValueError(f"GITHUB_APP_PRIVATE_KEY_FILE is not a PEM private key: {path}")
    tenant = value["TENANT_ID"]
    store.put(
        CredentialKey(tenant, "team-bot", "github"),
        {
            "app_id": value["GITHUB_APP_ID"],
            "private_key": private_key,
            "installation_id": value["GITHUB_APP_INSTALLATION_ID"],
        },
    )
    return [f"github team-bot for {tenant}: GitHub App {value['GITHUB_APP_ID']}"]
