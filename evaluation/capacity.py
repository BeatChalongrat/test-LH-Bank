"""Open-loop HTTP capacity test against the isolated Compose benchmark stack."""

import argparse
import asyncio
import hashlib
import http.client
import json
import math
import random
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
import time
from collections import Counter
from pathlib import Path
from uuid import uuid4

import httpx

from .capacity_workload import EXECUTIVE, HOT, cold_case

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = [
    "docker",
    "compose",
    "-p",
    "folio-capacity",
    "-f",
    "compose.yaml",
    "-f",
    "evaluation/capacity.compose.yaml",
]


def sql(statement):
    return subprocess.run(
        COMPOSE
        + [
            "exec",
            "-T",
            "db",
            "psql",
            "-XqAt",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            "desk_owner",
            "-d",
            "capacity",
        ],
        cwd=ROOT,
        input=statement,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()


def percentile(values, p):
    if values:
        return round(sorted(values)[math.ceil(p * len(values)) - 1], 2)
    return None


def stats(values):
    return {f"p{p}_ms": percentile(values, p / 100) for p in (50, 95, 99)}


async def run(args):
    async with httpx.AsyncClient(timeout=3) as probe:
        for _ in range(60):
            try:
                if (await probe.get("http://127.0.0.1:18770/health/ready")).is_success:
                    break
            except httpx.HTTPError:
                pass
            await asyncio.sleep(1)
        else:
            raise RuntimeError("The isolated benchmark API did not become ready")
    users = json.loads((ROOT / "var/capacity/identities.json").read_text())
    if len(users) != 5000:
        raise ValueError("Provision 5,000 isolated benchmark identities first")
    # Reset only this disposable project's answer cache. Operational data stays in the live stack.
    if sql("SELECT current_database()") != "capacity":
        raise RuntimeError("Answer cache reset requires the isolated capacity database")
    sql("TRUNCATE answers")
    rng = random.Random(6407)
    subjects = list(range(len(users)))
    rng.shuffle(subjects)
    expected_hot = []
    for _ in range(math.ceil(args.count / 10)):
        block = [True] * 6 + [False] * 4
        rng.shuffle(block)
        expected_hot.extend(block)
    results = []
    rows = ROOT / "var/capacity" / (args.out.stem + ".jsonl")
    cold_index = 0
    seen = set()
    plan = []
    for i, is_hot in enumerate(expected_hot[: args.count]):
        uid = subjects[i % len(subjects)]
        role = users[uid][0]
        if is_hot:
            questions = HOT + ([EXECUTIVE] if role == "executive" else [])
            item = rng.choice(questions)
        else:
            item = cold_case(cold_index)
            cold_index += 1
            if item[0] in seen:
                raise ValueError("Cold question repeated in schedule")
            seen.add(item[0])
        plan.append((uid, is_hot, item))

    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:18770",
        timeout=45,
        limits=httpx.Limits(max_connections=512, max_keepalive_connections=64),
    ) as client:
        for role in ("employee", "executive", "reviewer"):
            key = next(token for r, token in users if r == role)
            for question, doc, fact in HOT + (
                [EXECUTIVE] if role == "executive" else []
            ):
                resp = await client.post(
                    "/api/ask",
                    headers={"Authorization": "Bearer " + key},
                    json={"question": question},
                )
                result = resp.raise_for_status().json()
                if (
                    result["status"] != "answered"
                    or result["sources"][0]["doc_id"] != doc
                    or fact not in result["answer"]
                ):
                    raise RuntimeError(
                        f"warm-up failed {role} {doc} {question} {result['reason']}"
                    )
        boundary = float(sql("SELECT coalesce(max(ts),0) FROM events"))
        started = time.perf_counter()
        worker = threading.local()
        connections = []
        lock = threading.Lock()
        futures = []
        with rows.open("w", encoding="utf8") as raw, ThreadPoolExecutor(
            max_workers=256
        ) as pool:

            def send(i, uid, is_hot, item, target):
                question, doc, fact = item
                launch = time.perf_counter()
                result = {
                    "i": i,
                    "user": uid,
                    "planned_hit": is_hot,
                    "scheduled_s": round(target - started, 6),
                    "launch_lag_ms": round((launch - target) * 1000, 2),
                    "trace": None,
                    "hit": False,
                    "ok": False,
                    "code": 0,
                }
                try:
                    endpoint = "/api/ask"
                    payload = {"question": question}
                    if args.chat:
                        endpoint = f"/api/conversations/{uuid4()}/messages"
                        payload["turn_id"] = str(uuid4())
                    if not hasattr(worker, "connection"):
                        worker.connection = http.client.HTTPConnection(
                            "127.0.0.1", 18770, timeout=45
                        )
                        connections.append(worker.connection)
                    connection = worker.connection
                    if launch - getattr(worker, "last_used", 0) >= 4:
                        connection.close()
                    connection.request(
                        "POST",
                        endpoint,
                        body=json.dumps(payload).encode(),
                        headers={
                            "Authorization": "Bearer " + users[uid][1],
                            "Content-Type": "application/json",
                        },
                    )
                    response = connection.getresponse()
                    result["code"] = response.status
                    body = json.loads(response.read())
                    worker.last_used = time.perf_counter()
                    result.update(
                        trace=body["trace_id"],
                        hit=body["cached"],
                        reason=body["reason"],
                        ok=body["status"] == "answered"
                        and [s["doc_id"] for s in body["sources"]] == [doc]
                        and fact in body["answer"],
                    )
                except (
                    OSError,
                    http.client.HTTPException,
                    ValueError,
                    KeyError,
                ) as exc:
                    worker.connection.close()
                    result["reason"] = type(exc).__name__
                result["ms"] = round((time.perf_counter() - target) * 1000, 2)
                with lock:
                    results.append(result)
                    raw.write(json.dumps(result) + "\n")

            for i, (uid, is_hot, item) in enumerate(plan):
                target = started + i / args.rps
                time.sleep(max(0, target - time.perf_counter()))
                futures.append(pool.submit(send, i, uid, is_hot, item, target))
                if i and i % max(1, int(args.rps * 30)) == 0:
                    with lock:
                        raw.flush()
                        completed = [s["ms"] for s in results]
                    print(
                        f"scheduled {i}/{args.count}, completed {len(completed)}, outstanding {i+1-len(completed)}, p95 {percentile(completed, .95)} ms",
                        flush=True,
                    )
            for future in futures:
                future.result()
        for connection in connections:
            connection.close()
    elapsed = time.perf_counter() - started
    groups = {
        name: [s["ms"] for s in results if s["planned_hit"] == hot]
        for name, hot in (("cold", False), ("warm", True))
    }
    traced = json.loads(
        sql(
            f"""SELECT coalesce(json_agg(json_build_object('id',trace,'s',spans)),'[]') FROM events WHERE ts > {boundary}"""
        )
    )
    traces = {r["id"]: r["s"] for r in traced}
    spans = [traces[r["trace"]] for r in results if r["trace"] in traces]
    modeled = [r for r in spans if "generation_ms" in r]
    residual = [
        max(
            0,
            r.get("llm_http_ms", r["generation_ms"] - r.get("reader_ms", 0))
            - r["prompt_ms"]
            - r.get("predicted_ms", 0),
        )
        for r in modeled
        if "prompt_ms" in r
    ]
    report = {
        "corpus": "8 mock documents; one organization; role-scoped retrieval",
        "profile": "open-loop, seeded randomized 60% hot / 40% unique substantive scenario variants",
        "endpoint": "new_conversation_message" if args.chat else "api_ask",
        "target_rps": args.rps,
        "requests": args.count,
        "responses": len(results),
        "elapsed_s": round(elapsed, 3),
        "completed_rps": round(len(results) / elapsed, 2),
        "distinct_subjects": len(set(s["user"] for s in results)),
        "unique_cold_questions": len(seen),
        "actual_cache_hits": sum(s["hit"] for s in results),
        "cache_mix_mismatches": sum(s["hit"] != s["planned_hit"] for s in results),
        "verified_responses": sum(s["ok"] for s in results),
        "verified_within_3s": sum(s["ok"] and s["ms"] <= 3000 for s in results),
        "http_codes": dict(Counter(s["code"] for s in results)),
        "reasons": dict(Counter(s["reason"] for s in results)),
        "all": stats([s["ms"] for s in results]),
        "cold": stats(groups["cold"]),
        "warm": stats(groups["warm"]),
        "traces_joined": len(spans),
        "generation_calls": len(modeled),
        "decision_routes": dict(
            Counter(r.get("route", "unsuccessful") for r in modeled)
        ),
        "replica_completions": dict(
            Counter(str(r["replica"]) for r in modeled if "replica" in r)
        ),
        "server_admission": stats(
            [
                r.get("admission_ms", 0)
                for r in spans
                if not r.get("route") == "cache"
                and ("generation_ms" in r or r.get("admission_ms", 0) > 0)
            ]
        ),
        "reader_inference": stats(
            [r["reader_ms"] for r in modeled if "reader_ms" in r]
        ),
        "llm_http": stats([r["llm_http_ms"] for r in modeled if "llm_http_ms" in r]),
        "launch_lag": stats([r["launch_lag_ms"] for r in results]),
        "retrieval": stats([r["retrieval_ms"] for r in spans if "retrieval_ms" in r]),
        "model_roundtrip": stats([r["generation_ms"] for r in modeled]),
        "queue_and_transport_estimate": stats(residual),
        "prompt_compute": stats([r["prompt_ms"] for r in modeled if "prompt_ms" in r]),
        "tokens_evaluated": dict(
            prompt=sum(r.get("prompt_n", 0) for r in modeled),
            output=sum(r.get("predicted_n", 0) for r in modeled),
        ),
        "trace_log_sha256": hashlib.sha256(rows.read_bytes()).hexdigest(),
        "worst_minute_p95_ms": max(
            percentile(
                [s["ms"] for s in results if int(s["scheduled_s"] // 60) == m], 0.95
            )
            for m in sorted({int(s["scheduled_s"] // 60) for s in results})
        ),
    }
    report["passed"] = (
        len(results) == args.count == report["verified_responses"] == len(spans)
        and not report["cache_mix_mismatches"]
        and report["all"]["p95_ms"] <= 3000
        and report["cold"]["p95_ms"] <= 3000
        and report["warm"]["p95_ms"] <= 250
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf8")
    print(json.dumps(report), flush=True)
    return report["passed"]


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--rps", type=float, default=18)
    p.add_argument("--count", type=int, default=32400)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument(
        "--chat",
        action="store_true",
        help="Measure the persistent chat endpoint with a new thread per request",
    )
    args = p.parse_args()
    if not 0 < args.rps <= 100 or not 10 <= args.count <= 100000:
        p.error("invalid arrival schedule")
    raise SystemExit(0 if asyncio.run(run(args)) else 1)
