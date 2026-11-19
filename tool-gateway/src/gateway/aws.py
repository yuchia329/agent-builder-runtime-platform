"""What the gateway needs from AWS when it runs there (ADR 0018); nothing here runs locally unless
its setting is given.

- `kms_public_key`: the run-token public key (base64 DER) from the control plane's KMS key
  (`RUN_TOKEN_KMS_KEY_ID`), read once at startup.
- `ssm_secrets`: the gateway's own secrets from SSM (`SECRETS_SSM_PATH`), read once at startup and
  given to `Settings` as if they were environment variables. Never logged.
- `sigv4_signer`: signs calls to the control plane's internal API (`CONTROL_PLANE_SIGV4`), which
  API Gateway checks with IAM. A copy of the worker's: services share contracts, not code.
"""

import base64
import logging
from collections.abc import Callable, Mapping

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.credentials import Credentials
from botocore.exceptions import BotoCoreError, ClientError

log = logging.getLogger("gateway")

# Environment variable -> parameter name under SECRETS_SSM_PATH (infra/envs/dev/ssm.tf).
SECRET_PARAMETERS = {
    "GITHUB_OAUTH_CLIENT_SECRET": "github-oauth-client-secret",
    "OAUTH_STATE_SECRET": "oauth-state-secret",
}

# (method, url, headers, body) -> headers to send, including the signature.
Signer = Callable[[str, str, Mapping[str, str], bytes], dict[str, str]]


def aws_env(env: dict[str, str], kms=None, ssm=None) -> dict[str, str]:
    """The environment with what AWS holds filled in, before `Settings.from_env` reads it:
    `RUN_TOKEN_KMS_KEY_ID` becomes `RUN_TOKEN_PUBLIC_KEY`, and the secrets under
    `SECRETS_SSM_PATH` become their environment variables. Without those settings (local runs)
    the environment is returned unchanged and AWS is never called."""
    env = dict(env)
    if key_id := env.get("RUN_TOKEN_KMS_KEY_ID", "").strip():
        if env.get("RUN_TOKEN_PUBLIC_KEY", "").strip():
            raise ValueError("Set RUN_TOKEN_KMS_KEY_ID or RUN_TOKEN_PUBLIC_KEY, not both")
        env["RUN_TOKEN_PUBLIC_KEY"] = kms_public_key(key_id, kms)
        log.info("Run token public key from KMS key %s", key_id)
    if path := env.get("SECRETS_SSM_PATH", "").strip():
        found = ssm_secrets(path, ssm)
        clash = [name for name in found if env.get(name, "").strip()]
        if clash:
            raise ValueError(f"Set {', '.join(clash)} in SSM or the environment, not both")
        env.update(found)
        log.info("Secrets from SSM under %s: %s", path, ", ".join(sorted(found)) or "none")
    return env


def kms_public_key(key_id: str, kms=None) -> str:
    """The public key of an ECC_NIST_P256 SIGN_VERIFY key, as one-line base64 DER."""
    kms = kms or boto3.client("kms")
    try:
        answer = kms.get_public_key(KeyId=key_id)
    except (ClientError, BotoCoreError) as e:
        raise ValueError(
            f"Could not read the public key of KMS key {key_id} ({type(e).__name__})"
        ) from None
    # KMS answers both KeySpec and its older name CustomerMasterKeySpec; moto only the old one.
    spec = answer.get("KeySpec") or answer.get("CustomerMasterKeySpec")
    if spec != "ECC_NIST_P256" or answer.get("KeyUsage") != "SIGN_VERIFY":
        raise ValueError(
            f"KMS key {key_id} must be ECC_NIST_P256 for SIGN_VERIFY, is "
            f"{spec} for {answer.get('KeyUsage')}"
        )
    return base64.b64encode(answer["PublicKey"]).decode()


def ssm_secrets(path: str, ssm=None) -> dict[str, str]:
    """The gateway's secrets found under `path`, by environment variable name. A missing one is
    left out (its feature stays off, as with an unset variable) and logged by name."""
    if not path.startswith("/"):
        raise ValueError("SECRETS_SSM_PATH must start with /")
    ssm = ssm or boto3.client("ssm")
    prefix = path.rstrip("/")
    names = {f"{prefix}/{parameter}": env for env, parameter in SECRET_PARAMETERS.items()}
    try:
        answer = ssm.get_parameters(Names=list(names), WithDecryption=True)
    except (ClientError, BotoCoreError) as e:
        raise ValueError(f"Could not read secrets under {prefix} ({type(e).__name__})") from None
    if answer.get("InvalidParameters"):
        log.warning("SSM parameters missing: %s", ", ".join(answer["InvalidParameters"]))
    return {names[p["Name"]]: p["Value"] for p in answer.get("Parameters", [])}


def sigv4_signer(credentials: Callable[[], Credentials], region: str) -> Signer:
    """Signs with fresh credentials on every request (Lambda role credentials rotate)."""

    def sign(method: str, url: str, headers: Mapping[str, str], body: bytes) -> dict[str, str]:
        request = AWSRequest(method=method, url=url, data=body, headers=dict(headers))
        SigV4Auth(credentials(), "execute-api", region).add_auth(request)
        return dict(request.headers.items())

    return sign


def default_signer(region: str) -> Signer:
    session = boto3.Session()

    def credentials() -> Credentials:
        found = session.get_credentials()
        if found is None:
            raise RuntimeError("no AWS credentials to sign control plane requests")
        return found.get_frozen_credentials()

    return sigv4_signer(credentials, region)
