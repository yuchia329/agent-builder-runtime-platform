"""RunTokenVerifier: what a run token must be for the gateway to accept it."""

import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from gateway_testing import CONTRACTS, Keys, b64_body, claims, token

from gateway.auth.run_token import RunTokenVerifier, TokenRejected, load_public_key

NOW = 1_795_042_460


def verifier(keys: Keys, now: float = NOW) -> RunTokenVerifier:
    return RunTokenVerifier(keys.public_pem, CONTRACTS, now=lambda: now)


def at(**overrides):
    return claims(**{"iat": NOW, "exp": NOW + 180, **overrides})


def test_valid_token_gives_its_claims(keys):
    result = verifier(keys).verify(token(keys, at(require_approval=["github.add_label"])))

    assert result.run_id == "run_test"
    assert result.tenant == "tenant-test"
    assert result.tools == {"github.search_issues", "github.add_label"}
    assert result.require_approval == {"github.add_label"}
    assert result.started_by == "webhook:github"
    assert result.acts_as == "team-bot"


def test_key_id_is_the_public_key_thumbprint(keys):
    assert verifier(keys).key_id == keys.kid


def test_token_signed_by_another_key_is_refused(keys):
    other = Keys()
    forged = token(other, at(), kid=keys.kid)  # claims our key id, signed with another key

    with pytest.raises(TokenRejected, match="InvalidSignature"):
        verifier(keys).verify(forged)


def test_unknown_key_id_is_refused(keys):
    with pytest.raises(TokenRejected, match="key id"):
        verifier(keys).verify(token(keys, at(), kid="rotated-away"))


@pytest.mark.parametrize(
    ("now", "accepted"),
    [(NOW + 180 + 30, True), (NOW + 180 + 31, False), (NOW - 30, True), (NOW - 31, False)],
    ids=["exp within leeway", "expired", "iat within leeway", "issued in the future"],
)
def test_expiry_and_issue_time_allow_30_seconds_of_clock_difference(keys, now, accepted):
    check = verifier(keys, now)
    if accepted:
        check.verify(token(keys, at()))
    else:
        with pytest.raises(TokenRejected):
            check.verify(token(keys, at()))


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"aud": "worker"}, "InvalidAudience"),
        ({"iss": "someone"}, "InvalidIssuer"),
        ({"github_token": "ghs_x"}, "contract"),
        ({"tools": []}, "contract"),
        ({"started_by": "nobody"}, "contract"),
        ({"require_approval": ["github.close_issue"]}, "subset"),
        ({"exp": NOW}, "exp not after iat"),
    ],
)
def test_wrong_claims_are_refused(keys, change, reason):
    with pytest.raises(TokenRejected, match=reason):
        verifier(keys).verify(token(keys, at(**change)))


def test_missing_expiry_is_refused(keys):
    payload = at()
    del payload["exp"]

    with pytest.raises(TokenRejected):
        verifier(keys).verify(token(keys, payload))


def test_other_algorithms_are_refused(keys):
    hs256 = jwt.encode(at(), "s" * 32, algorithm="HS256", headers={"kid": keys.kid})
    unsigned = jwt.encode(at(), None, algorithm="none", headers={"kid": keys.kid})

    for bad in (hs256, unsigned, "not-a-jwt"):
        with pytest.raises(TokenRejected):
            verifier(keys).verify(bad)


def test_public_key_as_one_line_or_escaped_newlines(keys):
    assert RunTokenVerifier(b64_body(keys.public_pem), CONTRACTS).key_id == keys.kid
    escaped = keys.public_pem.replace("\n", "\\n")
    assert RunTokenVerifier(escaped, CONTRACTS).key_id == keys.kid


def test_other_curves_and_garbage_are_refused_at_startup():
    with pytest.raises(ValueError, match="P-256"):
        load_public_key(Keys(ec.SECP384R1()).public_pem)
    with pytest.raises(ValueError, match="X.509"):
        load_public_key("bm90IGEga2V5")


def test_real_clock_accepts_a_fresh_token(keys):
    fresh = claims(iat=int(time.time()), exp=int(time.time()) + 60)

    assert (
        RunTokenVerifier(keys.public_pem, CONTRACTS).verify(token(keys, fresh)).run_id == "run_test"
    )
