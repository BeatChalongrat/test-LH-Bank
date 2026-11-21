"""Evaluate the actual encoder, database and LLM. Use an isolated store to avoid polluting operational logs."""

import argparse
import json
import os
import tempfile
from pathlib import Path

from knowledge.db import ROOT, engine_for, initialize, revision
from knowledge.embedding_api import configured_encoder
from knowledge.ingest import ingest
from knowledge.search import retrieve
from knowledge.security import User
from knowledge.service import Generator, Service


def run(cases, out):
    encoder = configured_encoder()
    if os.getenv("EMBEDDING_MODE") == "managed":
        from knowledge.provider_profiles import active_profile

        with encoder.engine.connect() as conn:
            profile = active_profile(conn, "chat")
        if not profile:
            raise ValueError("Select a chat provider in Admin")
        generator = Generator(profile)
    else:
        generator = Generator()
    with tempfile.TemporaryDirectory() as temp:
        engine = engine_for("sqlite:///" + Path(temp, "evaluation.db").as_posix())
        initialize(engine)
        ingest(engine, encoder, ROOT / "data/catalog.json")
        service = Service(engine, encoder, generator)
        results = []
        for role, q, expected, ref, fact in json.loads(
            Path(cases).read_text(encoding="utf-8")
        ):
            with engine.connect() as conn:
                top = retrieve(conn, encoder, q, role, revision(conn)) if ref else []
            r = service.ask(User("evaluation", role), q)
            cited = [s["doc_id"] for s in r["sources"]]
            ids = list(dict.fromkeys(row["doc_id"] for row in top))
            ok = r["status"] == expected and (
                not ref or (ref in cited and fact in r["answer"])
            )
            results.append(
                dict(
                    question=q,
                    role=role,
                    expected=expected,
                    expected_doc=ref,
                    status=r["status"],
                    cited=cited,
                    score=r["confidence"],
                    reason=r["reason"],
                    correct=ok,
                    retrieved=ids,
                    expected_in_top3=ref in ids if ref else None,
                    answer_has_expected_fact=fact in r["answer"] if fact else None,
                    exact_evidence=any(r["answer"] in s["body"] for s in r["sources"])
                    if r["status"] == "answered"
                    else None,
                )
            )
        engine.dispose()
    generator.client.close()
    gold = [r for r in results if r["expected_doc"]]
    answered = [r for r in results if r["status"] == "answered"]
    hit = sum(r["expected_in_top3"] for r in gold)
    summary = dict(
        cases=len(results),
        correct=sum(r["correct"] for r in results),
        answered=len(answered),
        retrieval_precision_at_3=sum(
            (r["expected_in_top3"] / len(r["retrieved"])) if r["retrieved"] else 0
            for r in gold
        )
        / len(gold),
        retrieval_recall_at_3=hit / len(gold),
        verbatim_grounding_rate=sum(r["exact_evidence"] for r in answered)
        / len(answered)
        if answered
        else None,
        model=generator.model,
        threshold=service.threshold,
    )
    report = {"summary": summary, "results": results}
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary))
    return summary["correct"] == len(results)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", default=ROOT / "evaluation/cases.json")
    parser.add_argument("--out", default=ROOT / "var/evaluation.json")
    args = parser.parse_args()
    raise SystemExit(0 if run(args.cases, args.out) else 1)
