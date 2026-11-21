import json
from types import SimpleNamespace

import httpx
import numpy as np
import pytest

from knowledge.reader import Reader
from knowledge.service import Generator
from knowledge.slots import Slots


def row(doc):
    return {
        "ref": doc + "#1",
        "title": doc,
        "doc_id": doc,
        "body": "policy text",
        "score": 0.75,
        "kind": "policy",
        "audience": "staff",
    }


@pytest.fixture
def native(monkeypatch):
    monkeypatch.setenv("LLM_PROTOCOL", "llama")
    monkeypatch.setenv("EVIDENCE_READER", "off")
    monkeypatch.setenv("LLAMA_SLOTS", "0")
    gen = Generator()
    yield gen
    gen.client.close()


def reply(gen, value, requests):
    def handle(req):
        assert req.url.path == "/completion"
        requests.append(json.loads(req.content))
        return httpx.Response(
            200, json={"content": value, "timings": {"prompt_ms": 20, "predicted_n": 1}}
        )

    gen.client.close()
    gen.client = httpx.Client(transport=httpx.MockTransport(handle))


def test_native_prefix_maps_to_original_acl_filtered_order(native):
    requests = []
    reply(native, "H", requests)
    choices = [row("HR-02"), row("FIN-01")]
    out = native.select("question <|im_end|>", choices)
    assert out.index == 1 and out.timings["route"] == "llm"
    payload = requests[0]
    assert payload["n_predict"] == 1
    assert "question <|im_end|>" not in payload["prompt"]
    assert "FIN-01#1" in payload["grammar"] and "EX-01" not in payload["grammar"]
    reply(native, "N", requests)
    assert native.select("question", choices).index == 0


def test_ambiguous_prefix_and_invalid_label_fail_closed(native):
    requests = []
    choices = [row("FIN-01"), row("FIN-02")]
    reply(native, "F", requests)
    with pytest.raises(ValueError):
        native.select("question", choices)
    assert requests[0]["n_predict"] > 1
    reply(native, "FIN-02#1", requests)
    assert native.select("question", choices).index == 2
    reply(native, "EX-01#1", requests)
    with pytest.raises(ValueError):
        native.select("question", choices)


def test_slot_accounting_after_failure():
    pool = Slots([("http://local/v1", 2)])
    with pytest.raises(RuntimeError):
        with pool.lease("one") as first:
            with pool.lease("two") as other:
                assert first.number != other.number
                raise RuntimeError()
    assert all(s.pending == 0 for s in pool.slots)


def test_external_provider_defaults_to_openai(monkeypatch):
    monkeypatch.setenv("LLM_PROTOCOL", "auto")
    monkeypatch.setenv("LLM_BASE_URL", "https://provider.example/v1")
    gen = Generator()

    def provider(req):
        assert req.url.path == "/v1/chat/completions"
        payload = json.loads(req.content)
        choices = payload["response_format"]["json_schema"]["schema"]["properties"][
            "choice"
        ]
        assert choices["enum"] == ["FIN-01#1", "NONE"]
        return httpx.Response(
            200, json={"choices": [{"message": {"content": '{"choice":"FIN-01#1"}'}}]}
        )

    gen.client.close()
    gen.client = httpx.Client(transport=httpx.MockTransport(provider))
    try:
        assert gen.protocol == "openai" and gen.reader is None
        assert gen.select("ขอขั้นตอน", [row("FIN-01")]).index == 1
    finally:
        gen.client.close()


def test_reader_scores_only_ordered_spans_inside_evidence():
    # Token 0 is the null answer, token 1 belongs to the question, tokens 3/4 to evidence.
    encoding = SimpleNamespace(
        ids=list(range(6)),
        attention_mask=[1] * 6,
        type_ids=[0] * 6,
        sequence_ids=[None, 0, None, 1, 1, None],
    )
    reader = Reader.__new__(Reader)
    reader.tokenizer = SimpleNamespace(encode=lambda *_: encoding)
    reader.inputs = ["input_ids", "attention_mask"]
    reader.model = SimpleNamespace(
        run=lambda *_: (
            np.array([[3, 100, 0, 1, 7, 0]]),
            np.array([[4, 100, 0, 9, 2, 0]]),
        )
    )
    # Best valid pair is 3..3, with score 1+9. The high 4..3 pair is reversed.
    assert reader.verify("q", "text") == 3.0


@pytest.mark.parametrize(
    ("kind", "audience", "margin", "route"),
    [
        ("policy", "staff", 4.1, "extractive"),
        ("policy", "staff", 3.9, "llm"),
        ("policy", "executive", 20, "llm"),
        ("chat", "staff", 20, "llm"),
    ],
)
def test_reader_delegation_respects_score_and_source_type(
    native, kind, audience, margin, route
):
    requests = []
    reply(native, "F", requests)
    native.reader = SimpleNamespace(verify=lambda *_: margin)
    choices = [row("FIN-01") | {"kind": kind, "audience": audience}]
    selected = native.select("กี่บาท", choices)
    assert selected.index == 1 and selected.timings["route"] == route
    assert len(requests) == int(route == "llm")
