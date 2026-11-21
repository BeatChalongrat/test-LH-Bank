import json

import httpx
import numpy as np
import pytest
import sqlalchemy as sa

from knowledge.db import ROOT, chunks, engine_for, initialize, revision
from knowledge.embedding_api import ApiEncoder, EmbeddingUnavailable, configured_encoder
from knowledge.ingest import check_space, ingest, reembed
from knowledge.service import Generator
from knowledge.provider import ProviderCoolingDown


def api_encoder(monkeypatch, handler):
    monkeypatch.setenv("EMBEDDING_MODE", "api")
    monkeypatch.setenv("EMBEDDING_BASE_URL", "https://embedding.example/v1")
    monkeypatch.setenv("EMBEDDING_MODEL", "embedding-deployment")
    enc = configured_encoder()
    assert isinstance(enc, ApiEncoder)
    enc.client.close()
    enc.client = httpx.Client(transport=httpx.MockTransport(handler))
    return enc


def test_embeddings_reorder_validate_and_normalize(monkeypatch):
    def respond(req):
        assert req.url.path == "/v1/embeddings"
        body = json.loads(req.content)
        assert body["dimensions"] == 384
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": i, "embedding": [0.0] * i + [2.0] + [0.0] * (383 - i)}
                    for i in reversed(range(len(body["input"])))
                ]
            },
        )

    enc = api_encoder(monkeypatch, respond)
    with enc.client:
        actual = enc.encode(["first", "second"], "passage")
    assert np.array_equal(actual, np.eye(2, 384))


@pytest.mark.parametrize(
    "data",
    [
        [],
        [{"index": 0, "embedding": [0] * 384}],
        [{"index": 1, "embedding": [1] * 384}],
        [{"index": 0, "embedding": [1]}],
    ],
)
def test_invalid_embedding_fails_closed(monkeypatch, data):
    enc = api_encoder(monkeypatch, lambda _: httpx.Response(200, json={"data": data}))
    with enc.client, pytest.raises(EmbeddingUnavailable):
        enc.encode(["text"], "query")


def test_chat_observes_provider_retry_after(monkeypatch):
    monkeypatch.setenv("LLM_PROTOCOL", "openai")
    gen = Generator()
    calls = []

    def limited(req):
        calls.append(req)
        return httpx.Response(429, headers={"Retry-After": "30"})

    gen.client.close()
    gen.client = httpx.Client(transport=httpx.MockTransport(limited))
    choices = [{"ref": "HR-01#1", "title": "HR", "body": "policy"}]
    with gen.client:
        with pytest.raises(httpx.HTTPStatusError):
            gen.select("question", choices)
        with pytest.raises(ProviderCoolingDown):
            gen.select("question", choices)
    assert len(calls) == 1


def test_offline_reembed_preserves_old_vectors_on_failure(tmp_path, monkeypatch):
    count = 0

    def respond(req):
        nonlocal count
        count += 1
        batch = json.loads(req.content)["input"]
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": i, "embedding": [1.0] * 384} for i in range(len(batch))
                ]
            },
        )

    enc = api_encoder(monkeypatch, respond)
    engine = engine_for("sqlite:///" + (tmp_path / "emb.db").as_posix())
    initialize(engine)
    with enc.client:
        report = ingest(engine, enc, ROOT / "data/catalog.json")
        ingest(engine, enc, ROOT / "data/catalog.json")
        assert count == 1
        with engine.begin() as conn:
            archived = [
                dict(row) | {"revision": "archived"}
                for row in conn.execute(sa.select(chunks)).mappings()
            ]
            conn.execute(chunks.insert(), archived)
        with engine.connect() as conn:
            before = conn.execute(sa.select(chunks.c.ref, chunks.c.embedding)).all()
            rev = revision(conn)
        enc.space = "api-new"
        with engine.connect() as conn, pytest.raises(ValueError, match="differs"):
            check_space(conn, enc)
        real = enc.encode
        batches = []

        def fail_second_batch(texts, prefix):
            batches.append(len(texts))
            if len(batches) == 2:
                raise EmbeddingUnavailable("interrupted")
            return np.eye(len(texts), 384, dtype=np.float32)

        monkeypatch.setattr(enc, "encode", fail_second_batch)
        with pytest.raises(EmbeddingUnavailable):
            reembed(engine, enc)
        with engine.connect() as conn:
            assert revision(conn) == rev
            assert (
                conn.execute(sa.select(chunks.c.ref, chunks.c.embedding)).all()
                == before
            )
        assert batches == [12, 4]
        monkeypatch.setattr(enc, "encode", real)
        assert reembed(engine, enc)["chunks_reembedded"] == 2 * report["chunks"]
        with engine.connect() as conn:
            check_space(conn, enc)
            assert revision(conn) != rev
    engine.dispose()


def test_chat_provider_only_receives_department_authorized_evidence(
    tmp_path, monkeypatch
):
    from knowledge.document_worker import work_once
    from knowledge.db import departments, department_members
    from knowledge.documents import Upload, enqueue_document

    from knowledge.security import User
    from knowledge.service import Service

    DOC_ID = "DOC-AC10000000000001"

    def vectors(req):
        batch = json.loads(req.content)["input"]
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": i, "embedding": [1.0] * 384} for i in range(len(batch))
                ]
            },
        )

    enc = api_encoder(monkeypatch, vectors)
    monkeypatch.setenv("LLM_PROTOCOL", "openai")
    monkeypatch.setenv("LLM_BASE_URL", "https://chat.example/v1")
    gen = Generator()
    gen.client.close()
    sent = []

    def chat(req):
        evidence = json.loads(json.loads(req.content)["messages"][-1]["content"])[
            "evidence"
        ]
        sent.append(evidence)
        choice = next(
            (r["id"] for r in evidence if r["id"].startswith(DOC_ID + "#")), "NONE"
        )
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps({"choice": choice})}}]
            },
        )

    gen.client = httpx.Client(transport=httpx.MockTransport(chat))
    engine = engine_for("sqlite:///" + (tmp_path / "hr.db").as_posix())
    initialize(engine)
    with enc.client, gen.client:
        ingest(engine, enc, ROOT / "data/catalog.json")
        with engine.begin() as conn:
            conn.execute(departments.insert().values(code="HR", name="HR"))
            conn.execute(
                department_members.insert().values(
                    department="HR", subject="local/demo.hr"
                )
            )
            upload = Upload(
                title="ระเบียบโครงการตรวจสุขภาพออร์คิด",
                owner="HR",
                filename="orchid.txt",
                departments={"HR"},
            )
            enqueue_document(
                conn,
                DOC_ID,
                upload,
                "โครงการตรวจสุขภาพออร์คิดเบิกได้สูงสุด 2,750 บาทต่อปี".encode(),
                "admin",
            )
        assert work_once(engine, enc)
        service = Service(engine, enc, gen)
        question = "โครงการตรวจสุขภาพออร์คิดเบิกได้สูงสุดกี่บาทต่อปี"
        hr = service.ask(User("local/demo.hr", "employee"), question)
        assert hr["sources"], (hr["reason"], [[r["id"] for r in rows] for rows in sent])
        assert hr["sources"][0]["doc_id"] == DOC_ID and "2,750" in hr["answer"]
        assert any(r["id"].startswith(DOC_ID) for r in sent[-1])
        sent.clear()
        other = service.ask(User("local/demo.non-hr", "employee"), question)
        assert other["status"] == "fallback" and not other["sources"]
        assert sent and all(
            "2,750" not in r["text"] and DOC_ID not in r["id"]
            for batch in sent
            for r in batch
        )
    engine.dispose()


def test_embedding_backoff_prevents_more_429_requests(monkeypatch):
    calls = []

    def limited(req):
        calls.append(req)
        return httpx.Response(429, headers={"Retry-After": "invalid"})

    enc = api_encoder(monkeypatch, limited)
    with enc.client:
        for _ in range(2):
            with pytest.raises(ProviderCoolingDown):
                enc.encode(["text"], "query")
    assert len(calls) == 1
