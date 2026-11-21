import json
from types import SimpleNamespace

import httpx
import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from knowledge.api import create_app
from knowledge.db import ROOT, engine_for, initialize, provider_profiles
from knowledge.embedding_api import ManagedEncoder
from knowledge.encoder import Encoder
from knowledge.ingest import ingest
from knowledge.provider_profiles import active_profile, catalog
from knowledge.security import provision
from knowledge.service import Generator, Service


@pytest.fixture
def admin_system(tmp_path, monkeypatch):
    monkeypatch.setenv("PROVIDER_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("LLM_PROTOCOL", "openai")
    eng = engine_for("sqlite:///" + (tmp_path / "admin.db").as_posix())
    initialize(eng)
    with eng.begin() as conn:
        keys = {
            role: {"Authorization": "Bearer " + provision(conn, role, role)}
            for role in ("employee", "admin")
        }
    encoder = Encoder()
    ingest(eng, encoder, ROOT / "data/catalog.json")
    gen = Generator()
    sent = []

    def respond(req):
        sent.append(req)
        payload = json.loads(req.content)
        if req.url.path.endswith("/messages"):
            assert (
                req.headers["x-api-key"] == "test-anthropic"
                and "authorization" not in req.headers
            )
            ref = payload["tools"][0]["input_schema"]["properties"]["choice"]["enum"][0]
            return httpx.Response(
                200,
                json={
                    "usage": {"input_tokens": 32, "output_tokens": 6},
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "choose_evidence",
                            "input": {"choice": ref},
                        }
                    ],
                },
            )
        assert req.headers["authorization"].startswith("Bearer test-")
        ref = payload["response_format"]["json_schema"]["schema"]["properties"][
            "choice"
        ]["enum"][0]
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps({"choice": ref})}}]},
        )

    gen.client.close()
    gen.client = httpx.Client(transport=httpx.MockTransport(respond))
    with gen.client, TestClient(create_app(Service(eng, encoder, gen))) as api:
        yield api, keys, eng, sent
    eng.dispose()


def test_admin_routes_rotate_revoke_and_cache(admin_system):
    api, keys, eng, sent = admin_system
    path = "/api/admin/providers"
    admin = keys["admin"]

    def add(kind):
        return api.post(
            path,
            headers=admin,
            json={
                "provider": kind,
                "purpose": "chat",
                "name": kind,
                "model": "approved-model",
                "api_key": "test-" + kind,
            },
        ).json()["id"]

    def select(identity, version):
        return api.post(
            "/api/admin/provider-routes/chat",
            headers=admin,
            json={"profile_id": identity, "version": version},
        )

    def ask():
        return api.post(
            "/api/ask",
            headers=keys["employee"],
            json={"question": "ค่าแท็กซี่เบิกได้สูงสุดกี่บาทต่อเที่ยว"},
        ).json()

    assert api.get(path, headers=keys["employee"]).status_code == 403
    assert (
        api.post(path + "/x/test", headers=keys["employee"], json={}).status_code == 403
    )
    for i, kind in enumerate(
        ("groq", "gemini", "openai", "openrouter", "anthropic"), 1
    ):
        identity = add(kind)
        assert select(identity, i).status_code == 200
        assert select(identity, i).status_code == 409
        assert ask()["status"] == "answered" and ask()["cached"]
        assert str(sent[-1].url).startswith(catalog()[kind][1] + "/")
        assert (
            api.post(
                path + "/" + identity + "/remove", headers=admin, json={"version": 1}
            ).status_code
            == 409
        )
    assert len(sent) == 5
    assert "test-anthropic" not in api.get(path, headers=admin).text
    with eng.connect() as conn:
        stored = conn.scalar(
            sa.select(provider_profiles.c.secret).where(
                provider_profiles.c.id == identity
            )
        )
        assert "test-anthropic" not in stored and stored.startswith("gAAAA")
    response = api.post(
        path + "/" + identity + "/key",
        headers=admin,
        json={"api_key": "rotated-secret", "version": 1},
    )
    assert response.status_code == 200 and "rotated-secret" not in response.text
    assert select(None, 6).status_code == 200
    assert (
        api.post(
            path + "/" + identity + "/remove", headers=admin, json={"version": 2}
        ).status_code
        == 200
    )
    invalid = api.post(
        path,
        headers=admin,
        json={
            "api_key": "secret-that-must-not-be-echoed",
            "bad": "secret-that-must-not-be-echoed",
        },
    )
    assert invalid.status_code == 422 and "must-not-be-echoed" not in invalid.text


def test_embedding_onboarding_and_incompatible_index(tmp_path, monkeypatch):
    monkeypatch.setenv("PROVIDER_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("EMBEDDING_MODE", "managed")
    engine = engine_for("sqlite:///" + (tmp_path / "setup.db").as_posix())
    initialize(engine)
    with engine.begin() as conn:
        key = provision(conn, "settings", "admin")
    admin = {"Authorization": "Bearer " + key}
    enc = ManagedEncoder(engine)
    with enc.client, TestClient(
        create_app(SimpleNamespace(engine=engine, encoder=enc))
    ) as api:
        assert api.get("/health/ready").json()["status"] == "setup_required"

        def profile(model):
            return api.post(
                "/api/admin/providers",
                headers=admin,
                json={
                    "provider": "openai",
                    "purpose": "embedding",
                    "model": model,
                    "name": model,
                    "api_key": "test-key",
                },
            ).json()["id"]

        route = "/api/admin/provider-routes/embedding"
        a, b = profile("one"), profile("two")
        assert (
            api.post(
                route, headers=admin, json={"profile_id": a, "version": 1}
            ).status_code
            == 200
        )
        assert (
            api.post(
                route, headers=admin, json={"profile_id": b, "version": 2}
            ).status_code
            == 409
        )
        with engine.connect() as conn:
            assert active_profile(conn, "embedding")["id"] == a
        assert enc.space.startswith("api-")
    engine.dispose()
