"""Put secret values into the SSM parameters Terraform made (infra/envs/dev/ssm.tf, ADR 0018).

Terraform creates every secret parameter with a random placeholder and never sees the real value.
This script copies the real values from the repo's .env into those parameters:

    cd infra
    uv run --with boto3 python scripts/put_secrets.py --status    # which still hold the placeholder
    uv run --with boto3 python scripts/put_secrets.py             # put every name set in ../.env
    uv run --with boto3 python scripts/put_secrets.py SLACK_BOT_TOKEN   # only these names
    uv run --with boto3 python scripts/put_secrets.py --generate TENANT_API_KEY   # new random value

The parameter names come from `terraform -chdir=envs/dev output -json secret_parameters`. Values
are read from the .env file and sent with boto3: never printed, never on a command line (so not in
shell history or `ps`). A parameter at version 1 still holds Terraform's placeholder.

`--generate` puts a new random value (32 random bytes, URL-safe) instead of the .env one, for
secrets that should differ from local development, like the tenant API key behind the public API.
Read it back when needed: aws ssm get-parameter --with-decryption --name <name>.
"""

import argparse
import json
import secrets
import subprocess
import sys
from pathlib import Path

import boto3

INFRA = Path(__file__).resolve().parent.parent

# Shortest value accepted from .env, per name; a shorter one is a local development value.
MIN_LENGTH = {"TENANT_API_KEY": 32, "OAUTH_STATE_SECRET": 32}


def parameters(env_dir: str) -> dict[str, str]:
    out = subprocess.run(
        ["terraform", f"-chdir={INFRA / env_dir}", "output", "-json", "secret_parameters"],
        check=True, capture_output=True, text=True,
    ).stdout
    return json.loads(out)


def read_env(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key] = value
    return values


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("names", nargs="*", help="env var names to put (default: all)")
    parser.add_argument("--status", action="store_true", help="show each parameter's version; put nothing")
    parser.add_argument("--generate", action="store_true", help="put a new random value instead of the .env one")
    parser.add_argument("--env-file", default=str(INFRA.parent / ".env"))
    parser.add_argument("--env-dir", default="envs/dev", help="Terraform environment folder under infra/")
    parser.add_argument("--profile", default="default")
    parser.add_argument("--region", default="us-east-1")
    args = parser.parse_args()
    if args.profile == "prod":
        sys.exit("This project never uses the prod profile.")

    params = parameters(args.env_dir)
    unknown = set(args.names) - params.keys()
    if unknown:
        sys.exit(f"Not a secret parameter: {', '.join(sorted(unknown))} (known: {', '.join(sorted(params))})")
    ssm = boto3.Session(profile_name=args.profile, region_name=args.region).client("ssm")

    if args.status:
        for env_name, name in sorted(params.items()):
            # Metadata only (describe), never the value.
            found = ssm.describe_parameters(
                ParameterFilters=[{"Key": "Name", "Option": "Equals", "Values": [name]}])["Parameters"]
            version = found[0]["Version"] if found else None
            state = "missing" if version is None else "placeholder" if version == 1 else "set"
            print(f"{name}  ({env_name})  version {version}  {state}")
        return 0

    if args.generate and not args.names:
        sys.exit("--generate needs the names to generate, for example --generate TENANT_API_KEY")

    env = {} if args.generate else read_env(Path(args.env_file))
    for env_name, name in sorted(params.items()):
        if args.names and env_name not in args.names:
            continue
        if args.generate:
            value, source = secrets.token_urlsafe(32), "a new random value"
        else:
            value, source = env.get(env_name, ""), env_name
            if not value:
                print(f"skip {name}: {env_name} not set in {args.env_file}")
                continue
            if len(value) < MIN_LENGTH.get(env_name, 1):
                print(f"skip {name}: {env_name} in .env is shorter than {MIN_LENGTH[env_name]} characters "
                      f"(a local value); use --generate {env_name}")
                continue
        version = ssm.put_parameter(Name=name, Value=value, Type="SecureString", Overwrite=True)["Version"]
        print(f"put  {name} from {source} (version {version})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
