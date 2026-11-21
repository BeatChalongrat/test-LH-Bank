import base64
import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from authlib.integrations.starlette_client.apps import StarletteOAuth2App
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from knowledge.api import create_app
from knowledge.db import accounts, engine_for, initialize, invites, sessions, tickets
from knowledge.web_auth import issue_invite


@pytest.fixture
def db(tmp_path):
    engine = engine_for("sqlite:///" + (tmp_path / "portal.db").as_posix())
    initialize(engine)
    yield engine
    engine.dispose()


def make_client(engine, base="http://127.0.0.1:8765"):
    return TestClient(create_app(SimpleNamespace(engine=engine)), base_url=base)


def invite(engine, name, role):
    with engine.begin() as conn:
        return issue_invite(conn, name, role)


HEADERS = {"X-Desk-Request": "1", "Origin": "http://127.0.0.1:8765"}


def sign(client, code):
    return client.post("/api/auth/invite", json={"code": code}, headers=HEADERS)


def test_revocable_single_use_cookie(db):
    with make_client(db) as c:
        code = invite(db, "personal", "employee")
        assert c.post("/api/auth/invite", json={"code": code}).status_code == 403
        assert (
            c.post(
                "/api/auth/invite",
                json={"code": code},
                headers=HEADERS | {"Origin": "https://other.test"},
            ).status_code
            == 403
        )
        r = sign(c, code)
        assert r.status_code == 200 and "HttpOnly" in r.headers["set-cookie"]
        assert sign(c, code).status_code == 401
        assert c.get("/api/me").json() == {
            "subject": "local/personal",
            "role": "employee",
        }
        assert c.post("/api/auth/logout").status_code == 403
        old = c.cookies.get("desk_session")
        assert c.post("/api/auth/logout", headers=HEADERS, json={}).is_success
        c.cookies.set("desk_session", old)
        assert c.get("/api/me").status_code == 401
        c.cookies.clear()
        assert sign(c, invite(db, "personal", "employee")).is_success
        with db.begin() as conn:
            conn.execute(accounts.delete().where(accounts.c.subject == "personal"))
        assert c.get("/api/me").status_code == 401


def test_expired_identity_credentials(db):
    with make_client(db) as c:
        code = invite(db, "staff", "employee")
        with db.begin() as conn:
            conn.execute(invites.update().values(expires=time.time() - 1))
        assert sign(c, code).status_code == 401
        assert sign(c, invite(db, "staff", "employee")).is_success
        with db.begin() as conn:
            conn.execute(sessions.update().values(expires=time.time() - 1))
        assert c.get("/api/me").status_code == 401


def test_single_case_owner_and_scope(db):
    with db.begin() as conn:
        for i, scope in enumerate(["staff", "executive"]):
            conn.execute(
                tickets.insert().values(
                    id=str(i),
                    subject="local/staff",
                    scope=scope,
                    created=time.time(),
                    state="open",
                    question="case",
                )
            )
    with make_client(db) as first, make_client(db) as second, make_client(
        db
    ) as employee:
        for client, name, role in [
            (first, "ra", "reviewer"),
            (second, "rb", "reviewer"),
            (employee, "staff", "employee"),
        ]:
            assert sign(client, invite(db, name, role)).is_success
        assert [r["id"] for r in employee.get("/api/tickets").json()] == ["0"]
        assert first.get("/api/workspace").json()["open"] == 1
        assert (
            first.post("/api/tickets/1/claim", headers=HEADERS, json={}).status_code
            == 404
        )
        assert (
            employee.post("/api/tickets/0/claim", headers=HEADERS, json={}).status_code
            == 403
        )

        def claim(c):
            return c.post("/api/tickets/0/claim", headers=HEADERS, json={}).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            codes = list(pool.map(claim, [first, second]))
        assert sorted(codes) == [200, 409]
        owner, peer = (first, second) if codes[0] == 200 else (second, first)
        assert claim(owner) == 200
        assert (
            peer.post("/api/tickets/0/release", headers=HEADERS, json={}).status_code
            == 409
        )
        assert owner.post("/api/tickets/0/release", headers=HEADERS, json={}).is_success
        assert claim(peer) == 200
        owner, peer = peer, owner
        assert (
            peer.post(
                "/api/tickets/0/resolve",
                headers=HEADERS,
                json={"resolution": "verified"},
            ).status_code
            == 409
        )
        assert owner.post(
            "/api/tickets/0/resolve", headers=HEADERS, json={"resolution": "verified"}
        ).is_success
        assert [
            e["action"] for e in employee.get("/api/tickets/0/activity").json()
        ] == ["opened", "claimed", "released", "claimed", "resolved"]
        assert employee.get("/api/tickets/1/activity").status_code == 404
        assert (
            employee.get("/api/tickets?state=resolved").json()[0]["resolution"]
            == "verified"
        )


@pytest.mark.parametrize(
    "invalid_claims", [{}, {"nonce": "other"}, {"aud": "other"}, {"groups": []}]
)
def test_browser_oidc_pkce_and_signed_identity(db, monkeypatch, invalid_claims):
    issuer, origin = "https://id.example.test", "https://portal.example.test"
    for key, value in dict(
        LOCAL_AUTH="0",
        OIDC_ISSUER=issuer,
        PORTAL_ORIGIN=origin,
        OIDC_CLIENT_ID="portal",
        OIDC_CLIENT_SECRET="test-secret",
        BROWSER_SIGNING_KEY="a" * 48,
        OIDC_GROUP_EMPLOYEE="staff",
    ).items():
        monkeypatch.setenv(key, value)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key())) | {
        "kid": "test",
        "alg": "RS256",
        "use": "sig",
    }
    auth = {}

    def provider(req):
        if req.url.path.endswith("openid-configuration"):
            return httpx.Response(
                200,
                json={
                    "issuer": issuer,
                    "authorization_endpoint": issuer + "/authorize",
                    "token_endpoint": issuer + "/token",
                    "jwks_uri": issuer + "/jwks",
                    "id_token_signing_alg_values_supported": ["RS256"],
                },
            )
        if req.url.path == "/jwks":
            return httpx.Response(200, json={"keys": [jwk]})
        assert req.url.path == "/token"
        form = parse_qs(req.content.decode())
        assert form["code"] == ["one-code"]
        challenge = (
            base64.urlsafe_b64encode(
                hashlib.sha256(form["code_verifier"][0].encode()).digest()
            )
            .decode()
            .rstrip("=")
        )
        assert auth["code_challenge"] == [challenge] and auth[
            "code_challenge_method"
        ] == ["S256"]
        now = int(time.time())
        claims = {
            "iss": issuer,
            "aud": "portal",
            "sub": "staff-123",
            "nonce": auth["nonce"][0],
            "iat": now,
            "exp": now + 300,
            "groups": ["staff"],
        }
        token = jwt.encode(
            claims | invalid_claims, key, algorithm="RS256", headers={"kid": "test"}
        )
        return httpx.Response(
            200,
            json={
                "access_token": "provider-access",
                "token_type": "Bearer",
                "id_token": token,
            },
        )

    original = StarletteOAuth2App.client_cls

    class IdentityClient(original):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs, transport=httpx.MockTransport(provider))

    monkeypatch.setattr(StarletteOAuth2App, "client_cls", IdentityClient)
    with make_client(db, origin) as c:
        assert c.get("/api/auth/options").json() == {
            "organization": True,
            "invitation": False,
        }
        link = c.get("/auth/login", follow_redirects=False)
        auth.update(parse_qs(urlsplit(link.headers["location"]).query))
        assert auth["redirect_uri"] == [origin + "/auth/callback"]
        params = {"code": "one-code", "state": auth["state"][0]}
        callback = c.get("/auth/callback", params=params, follow_redirects=False)
        if invalid_claims:
            assert callback.headers["location"] == "/?login=denied"
            assert c.get("/api/me").status_code == 401
            return
        assert callback.headers["location"] == "/"
        assert c.get("/api/me").json() == {
            "subject": "oidc/staff-123",
            "role": "employee",
        }
        cookie = callback.headers["set-cookie"]
        assert "Secure" in cookie and "HttpOnly" in cookie
        assert (
            c.get("/auth/callback", params=params, follow_redirects=False).headers[
                "location"
            ]
            == "/?login=denied"
        )
