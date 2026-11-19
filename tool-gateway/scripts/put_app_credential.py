"""Puts the GitHub App credential for acts_as team-bot into the SSM TokenStore (ADR 0018).

Locally the gateway reads the App from .env at startup (gateway.seed). On AWS an operator writes
it once with this script, through the same adapter the gateway reads with, so the parameter name
always matches:

    cd tool-gateway
    uv run python scripts/put_app_credential.py --status   # is it there? (no value shown)
    uv run python scripts/put_app_credential.py            # write it from ../.env

Reads TENANT_ID, GITHUB_APP_ID, GITHUB_APP_INSTALLATION_ID and GITHUB_APP_PRIVATE_KEY_FILE from
the .env file; the key file is read here and sent with boto3, never printed or put on a command
line. TENANT_ID must be the tenant the control plane runs (its TENANT_ID on AWS).
"""

import argparse
import sys
from pathlib import Path

import boto3

from gateway.adapters.token_store_memory import MemoryTokenStore
from gateway.adapters.token_store_ssm import SsmTokenStore
from gateway.ports.token_store import CredentialKey
from gateway.seed import seed_from_env

ROOT = Path(__file__).resolve().parent.parent


def read_env(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key.removeprefix("export ").strip()] = value
    return values


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--status", action="store_true", help="show whether it is stored; write nothing"
    )
    parser.add_argument("--env-file", default=str(ROOT.parent / ".env"))
    parser.add_argument("--path", default="/agent-platform/dev/gateway/tokens")
    parser.add_argument("--profile", default="default")
    parser.add_argument("--region", default="us-east-1")
    args = parser.parse_args()
    if args.profile == "prod":
        sys.exit("This project never uses the prod profile.")

    env = read_env(Path(args.env_file))
    tenant = env.get("TENANT_ID", "").strip()
    if not tenant:
        sys.exit(f"TENANT_ID is not set in {args.env_file}")
    key = CredentialKey(tenant, "team-bot", "github")
    ssm = boto3.Session(profile_name=args.profile, region_name=args.region).client("ssm")
    store = SsmTokenStore(ssm, args.path)
    name = store.name(key)

    if args.status:
        found = ssm.describe_parameters(
            ParameterFilters=[{"Key": "Name", "Option": "Equals", "Values": [name]}]
        )["Parameters"]
        print(f"{name}: " + (f"stored (version {found[0]['Version']})" if found else "missing"))
        return 0

    memory = MemoryTokenStore()
    described = seed_from_env(memory, env)  # same checks as a local start
    if not described:
        sys.exit(f"No GITHUB_APP_* settings in {args.env_file}")
    store.put(key, memory.get(key))
    print(f"put {name}: {described[0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
