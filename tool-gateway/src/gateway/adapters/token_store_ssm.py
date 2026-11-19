"""TokenStore in SSM Parameter Store (ADR 0018): one SecureString parameter per credential.

Name: `<path>/<tenant>/<identity>/<provider>`, for example
`/agent-platform/dev/gateway/tokens/tenant-demo/team-bot/github`. SSM names allow only letters,
digits and `_ . - /`, and identities can hold `:` (`user:42`), so every segment keeps
`A-Z a-z 0-9 . -` and writes any other byte, `_` included, as `_` plus two hex digits:
`user:42` -> `user_3A42`. Distinct keys always give distinct names.

Values are JSON, encrypted with the AWS managed key aws/ssm; standard tier, so at most 4 KB (a
GitHub App credential is about 1.7 KB). Nothing is cached: GitHub replaces a refresh token on each
refresh, and a stale copy on another Lambda instance would fail its refresh and log the owner out.
Errors name the parameter, never its value.
"""

import json
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from gateway.ports.token_store import CredentialKey

MAX_VALUE_BYTES = 4096  # SSM standard tier
SAFE = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789.-")


class TokenStoreError(Exception):
    """SSM refused or could not be reached; the message names the parameter only."""


def segment(text: str) -> str:
    """One name segment: safe characters kept, every other byte as `_XX`."""
    if not text:
        raise ValueError("A credential key segment cannot be empty")
    return "".join(ch if ch in SAFE else "".join(f"_{b:02X}" for b in ch.encode()) for ch in text)


class SsmTokenStore:
    def __init__(self, ssm, path: str) -> None:
        """`ssm`: a boto3 SSM client. `path`: the prefix, for example
        /agent-platform/dev/gateway/tokens."""
        if not path.startswith("/"):
            raise ValueError("TOKEN_STORE_SSM_PATH must start with /")
        self._ssm = ssm
        self._path = path.rstrip("/")

    def name(self, key: CredentialKey) -> str:
        parts = (key.tenant, key.identity, key.provider)
        return f"{self._path}/{'/'.join(segment(p) for p in parts)}"

    def get(self, key: CredentialKey) -> dict[str, Any] | None:
        name = self.name(key)
        try:
            answer = self._ssm.get_parameter(Name=name, WithDecryption=True)
        except ClientError as e:
            if _code(e) == "ParameterNotFound":
                return None
            raise TokenStoreError(f"Could not read {name}: {_code(e)}") from None
        except BotoCoreError as e:
            raise TokenStoreError(f"Could not read {name}: {type(e).__name__}") from None
        try:
            value = json.loads(answer["Parameter"]["Value"])
        except ValueError:
            raise TokenStoreError(f"{name} does not hold JSON") from None
        if not isinstance(value, dict):
            raise TokenStoreError(f"{name} does not hold a JSON object")
        return value

    def put(self, key: CredentialKey, value: dict[str, Any]) -> None:
        name = self.name(key)
        text = json.dumps(value, separators=(",", ":"))
        if len(text.encode()) > MAX_VALUE_BYTES:
            raise TokenStoreError(f"The value for {name} is over {MAX_VALUE_BYTES} bytes")
        try:
            self._ssm.put_parameter(Name=name, Value=text, Type="SecureString", Overwrite=True)
        except ClientError as e:
            raise TokenStoreError(f"Could not write {name}: {_code(e)}") from None
        except BotoCoreError as e:
            raise TokenStoreError(f"Could not write {name}: {type(e).__name__}") from None

    def delete(self, key: CredentialKey) -> bool:
        name = self.name(key)
        try:
            self._ssm.delete_parameter(Name=name)
        except ClientError as e:
            if _code(e) == "ParameterNotFound":
                return False
            raise TokenStoreError(f"Could not delete {name}: {_code(e)}") from None
        except BotoCoreError as e:
            raise TokenStoreError(f"Could not delete {name}: {type(e).__name__}") from None
        return True


def _code(error: ClientError) -> str:
    return error.response.get("Error", {}).get("Code", "error")
