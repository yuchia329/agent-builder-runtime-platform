"""The gateway on AWS (ADR 0018): SSM token store details, run-token key from KMS, secrets from
SSM, SigV4 for the control plane. AWS is imitated by moto; nothing leaves the process."""

import json

import boto3
import pytest
from botocore.credentials import Credentials
from fake_control_plane import FakeControlPlane
from gateway_testing import CONTRACTS, Keys
from moto import mock_aws

from gateway.__main__ import build
from gateway.adapters.control_plane_http import HttpControlPlane
from gateway.adapters.token_store_ssm import SsmTokenStore, TokenStoreError, segment
from gateway.auth.run_token import RunTokenVerifier, load_public_key, thumbprint
from gateway.aws import aws_env, sigv4_signer
from gateway.config import Settings
from gateway.ports.token_store import CredentialKey

REGION = "us-east-1"
PATH = "/test/gateway/tokens"
BOT = CredentialKey("tenant-a", "team-bot", "github")


@pytest.fixture(autouse=True)
def fake_credentials(monkeypatch):
    for name, value in (
        ("AWS_ACCESS_KEY_ID", "testing"),
        ("AWS_SECRET_ACCESS_KEY", "testing"),
        ("AWS_DEFAULT_REGION", REGION),
    ):
        monkeypatch.setenv(name, value)


@pytest.fixture
def ssm():
    with mock_aws():
        yield boto3.client("ssm", region_name=REGION)


@pytest.fixture
def kms():
    with mock_aws():
        yield boto3.client("kms", region_name=REGION)


# --- SSM token store -----------------------------------------------------------------------


def test_segments_keep_safe_characters_and_escape_the_rest():
    assert segment("team-bot") == "team-bot"
    assert segment("user:42") == "user_3A42"
    assert segment("a_b") == "a_5Fb"  # "_" itself is escaped, so names cannot collide
    assert segment("ü") == "_C3_BC"
    with pytest.raises(ValueError):
        segment("")


def test_names_and_values_in_ssm(ssm):
    store = SsmTokenStore(ssm, PATH + "/")
    store.put(CredentialKey("tenant-a", "user:42", "github"), {"refresh_token": "r"})

    parameter = ssm.get_parameter(Name=f"{PATH}/tenant-a/user_3A42/github", WithDecryption=True)
    assert parameter["Parameter"]["Type"] == "SecureString"
    assert json.loads(parameter["Parameter"]["Value"]) == {"refresh_token": "r"}


def test_values_over_4_kb_are_refused_by_name_without_the_value(ssm):
    store = SsmTokenStore(ssm, PATH)

    with pytest.raises(TokenStoreError, match="over 4096 bytes") as refused:
        store.put(BOT, {"private_key": "k" * 5000})

    assert "kkkk" not in str(refused.value)


def test_a_value_that_is_not_a_json_object_is_an_error(ssm):
    store = SsmTokenStore(ssm, PATH)
    ssm.put_parameter(Name=store.name(BOT), Value="[1]", Type="SecureString")

    with pytest.raises(TokenStoreError, match="JSON object"):
        store.get(BOT)


def test_ssm_errors_name_the_parameter(ssm, monkeypatch):
    store = SsmTokenStore(ssm, PATH)
    store.put(BOT, {"token": "secret-value"})

    def broken(**_):
        from botocore.exceptions import ClientError

        raise ClientError({"Error": {"Code": "AccessDeniedException"}}, "GetParameter")

    monkeypatch.setattr(ssm, "get_parameter", broken)
    with pytest.raises(TokenStoreError, match="AccessDeniedException") as failed:
        store.get(BOT)
    assert store.name(BOT) in str(failed.value)
    assert "secret-value" not in str(failed.value)


def test_a_path_must_be_absolute(ssm):
    with pytest.raises(ValueError, match="TOKEN_STORE_SSM_PATH"):
        SsmTokenStore(ssm, "gateway/tokens")


# --- Settings for AWS ----------------------------------------------------------------------


def test_ssm_store_needs_a_path():
    with pytest.raises(ValueError, match="TOKEN_STORE_SSM_PATH"):
        Settings.from_env({"RUN_TOKEN_PUBLIC_KEY": "k", "TOKEN_STORE": "ssm"})
    with pytest.raises(ValueError, match="memory or ssm"):
        Settings.from_env({"RUN_TOKEN_PUBLIC_KEY": "k", "TOKEN_STORE": "dynamodb"})


def test_sigv4_needs_a_region():
    with pytest.raises(ValueError, match="AWS_REGION"):
        Settings.from_env({"RUN_TOKEN_PUBLIC_KEY": "k", "CONTROL_PLANE_SIGV4": "true"})
    with pytest.raises(ValueError, match="true or false"):
        Settings.from_env({"RUN_TOKEN_PUBLIC_KEY": "k", "CONTROL_PLANE_SIGV4": "yes"})


def test_build_with_the_ssm_store_reads_credentials_from_ssm(ssm):
    keys = Keys()
    settings = Settings.from_env(
        {
            "RUN_TOKEN_PUBLIC_KEY": keys.public_pem,
            "CONNECTORS": "fake",
            "TOKEN_STORE": "ssm",
            "TOKEN_STORE_SSM_PATH": PATH,
            # Ignored with the SSM store: an operator writes it (scripts/put_app_credential.py).
            "GITHUB_APP_ID": "1",
        }
    )

    assert build(settings) is not None  # no seeding from the environment, so no key file needed


# --- Run-token key from KMS ----------------------------------------------------------------


def test_the_kms_public_key_becomes_the_run_token_key(kms):
    key_id = kms.create_key(KeyUsage="SIGN_VERIFY", KeySpec="ECC_NIST_P256")["KeyMetadata"]["KeyId"]
    kms.create_alias(AliasName="alias/run-token", TargetKeyId=key_id)

    env = aws_env({"RUN_TOKEN_KMS_KEY_ID": "alias/run-token"}, kms=kms)

    der = kms.get_public_key(KeyId=key_id)["PublicKey"]
    from cryptography.hazmat.primitives.serialization import load_der_public_key

    expected = thumbprint(load_der_public_key(der))
    assert thumbprint(load_public_key(env["RUN_TOKEN_PUBLIC_KEY"])) == expected
    assert RunTokenVerifier(env["RUN_TOKEN_PUBLIC_KEY"], CONTRACTS).key_id == expected


def test_kms_and_a_configured_key_together_are_refused(kms):
    with pytest.raises(ValueError, match="not both"):
        aws_env({"RUN_TOKEN_KMS_KEY_ID": "alias/x", "RUN_TOKEN_PUBLIC_KEY": "k"}, kms=kms)


def test_a_kms_key_of_the_wrong_kind_is_refused(kms):
    key_id = kms.create_key(KeyUsage="ENCRYPT_DECRYPT", KeySpec="SYMMETRIC_DEFAULT")["KeyMetadata"][
        "KeyId"
    ]

    with pytest.raises(ValueError, match="ECC_NIST_P256 for SIGN_VERIFY"):
        aws_env({"RUN_TOKEN_KMS_KEY_ID": key_id}, kms=kms)


def test_a_missing_kms_key_names_the_key(kms):
    with pytest.raises(ValueError, match="alias/missing"):
        aws_env({"RUN_TOKEN_KMS_KEY_ID": "alias/missing"}, kms=kms)


# --- Secrets from SSM ----------------------------------------------------------------------


def test_secrets_under_the_path_fill_the_environment(ssm):
    for name, value in (("oauth-state-secret", "s" * 40), ("github-oauth-client-secret", "c")):
        ssm.put_parameter(Name=f"/test/gateway/{name}", Value=value, Type="SecureString")

    env = aws_env({"SECRETS_SSM_PATH": "/test/gateway/", "OTHER": "kept"}, ssm=ssm)

    assert env["OAUTH_STATE_SECRET"] == "s" * 40
    assert env["GITHUB_OAUTH_CLIENT_SECRET"] == "c"
    assert env["OTHER"] == "kept"


def test_a_missing_secret_stays_unset(ssm):
    ssm.put_parameter(Name="/test/gateway/oauth-state-secret", Value="s" * 40, Type="SecureString")

    env = aws_env({"SECRETS_SSM_PATH": "/test/gateway"}, ssm=ssm)

    assert "GITHUB_OAUTH_CLIENT_SECRET" not in env


def test_a_secret_in_both_places_is_refused(ssm):
    ssm.put_parameter(Name="/test/gateway/oauth-state-secret", Value="s" * 40, Type="SecureString")

    with pytest.raises(ValueError, match="not both"):
        aws_env({"SECRETS_SSM_PATH": "/test/gateway", "OAUTH_STATE_SECRET": "x"}, ssm=ssm)


def test_without_aws_settings_nothing_changes():
    env = {"RUN_TOKEN_PUBLIC_KEY": "k"}

    assert aws_env(env) == env


# --- SigV4 to the control plane ------------------------------------------------------------


def test_control_plane_calls_are_signed_for_execute_api():
    signer = sigv4_signer(lambda: Credentials("AKIDEXAMPLE", "secret"), REGION)
    with FakeControlPlane() as plane:
        plane.put({"run_id": "run_1", "tenant_id": "t", "status": "RUNNING"})

        HttpControlPlane(plane.url, signer=signer).get_run("run_1")

    _, headers = plane.requests[-1]
    authorization = headers["Authorization"]
    assert authorization.startswith("AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/")
    assert f"/{REGION}/execute-api/aws4_request" in authorization
    assert "X-Amz-Date" in headers
