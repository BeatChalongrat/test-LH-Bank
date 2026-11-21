import time
from types import SimpleNamespace

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from knowledge import security


def test_signed_access_token_requires_issuer_audience_expiry_and_group(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    issuer = "https://identity.example.test"
    for name, value in {
        "LOCAL_AUTH": "0",
        "OIDC_ISSUER": issuer,
        "OIDC_JWKS_URL": issuer + "/jwks",
        "OIDC_AUDIENCE": "desk",
        "OIDC_GROUP_EMPLOYEE": "desk-staff",
        "OIDC_GROUP_EXECUTIVE": "desk-board",
        "OIDC_GROUP_ADMIN": "desk-ops",
    }.items():
        monkeypatch.setenv(name, value)
    keyset = SimpleNamespace(
        get_signing_key_from_jwt=lambda _: SimpleNamespace(key=key.public_key())
    )
    monkeypatch.setattr(security, "keyset", lambda _: keyset)
    claims = {
        "iss": issuer,
        "aud": "desk",
        "sub": "jane",
        "exp": int(time.time()) + 120,
        "iat": int(time.time()) - 60,
        "groups": ["desk-staff"],
        "role": "executive",
    }

    def signed(**changes):
        return jwt.encode(
            claims | changes, key, algorithm="RS256", headers={"kid": "test-key"}
        )

    assert security.authenticate(None, signed()) == security.User(
        "oidc/jane", "employee"
    )
    assert (
        security.authenticate(None, signed(groups=["desk-board"])).role == "executive"
    )
    assert (
        security.authenticate(None, signed(groups=["desk-ops", "desk-board"])).role
        == "admin"
    )
    for change in (
        {"aud": "other"},
        {"iss": "https://other.example.test"},
        {"exp": int(time.time()) - 1},
        {"groups": ["other"]},
        {"groups": "desk-staff"},
    ):
        assert security.authenticate(None, signed(**change)) is None
