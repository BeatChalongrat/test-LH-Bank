"""Paced HTTP benchmark for the warm answer path on the sample corpus."""

import argparse
import asyncio
import json
import math
import time
from collections import Counter
from pathlib import Path

import httpx


async def run(args):
    credentials = json.loads(Path(args.credentials).read_text(encoding="utf8"))
    token = (
        credentials["employee"]
        if isinstance(credentials, dict)
        else next(key for role, key in credentials if role == "employee")
    )
    warm_questions = [
        "ค่าแท็กซี่เบิกได้สูงสุดกี่บาทต่อเที่ยว",
        "ลาพักร้อนต้องยื่นล่วงหน้ากี่วัน",
        "สถานะเคส CS-1042 คืออะไร",
    ]
    questions = (
        json.loads(Path(args.workload).read_text(encoding="utf8"))
        if args.workload
        else warm_questions
    )
    async with httpx.AsyncClient(
        base_url=args.base, timeout=45, headers={"Authorization": "Bearer " + token}
    ) as client:
        for q in warm_questions:
            warm = await client.post("/api/ask", json={"question": q})
            if warm.raise_for_status().json()["status"] != "answered":
                raise RuntimeError("Warm-up failed")

        async def send(i, start):
            target = start + i / args.rps
            await asyncio.sleep(max(0, target - time.perf_counter()))
            try:
                res = await client.post(
                    "/api/ask", json={"question": questions[i % len(questions)]}
                )
                body = res.raise_for_status().json()
                return (
                    (time.perf_counter() - target) * 1000,
                    body["status"] == "answered",
                    body["cached"],
                    body["reason"],
                )
            except (httpx.HTTPError, ValueError):
                return (time.perf_counter() - target) * 1000, False, False, "http_error"

        start = time.perf_counter()
        samples = await asyncio.gather(*(send(i, start) for i in range(args.count)))
        elapsed = time.perf_counter() - start
    durations = sorted(s[0] for s in samples)
    cold = sorted(s[0] for s in samples if not s[2])
    mode = (
        "mixed HTTP; source questions in " + str(args.workload)
        if args.workload
        else "prewarmed exact-cache HTTP; three Thai questions; one employee; excludes model generation"
    )
    report = dict(
        mode=mode,
        requests=len(samples),
        target_rps=args.rps,
        elapsed_s=round(elapsed, 2),
        successful=sum(s[1] for s in samples),
        cache_hits=sum(s[2] for s in samples),
        cold_p95_ms=round(cold[math.ceil(0.95 * len(cold)) - 1], 2) if cold else None,
        reasons=dict(Counter(s[3] for s in samples)),
        p50_ms=round(durations[math.ceil(0.5 * len(samples)) - 1], 2),
        p95_ms=round(durations[math.ceil(0.95 * len(samples)) - 1], 2),
        p99_ms=round(durations[math.ceil(0.99 * len(samples)) - 1], 2),
    )
    print(json.dumps(report))
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2) + "\n", encoding="utf8")
    return report["successful"] == args.count


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--base", default="http://127.0.0.1:8765")
    p.add_argument("--credentials", default="var/compose/access.json")
    p.add_argument("--rps", type=int, default=20)
    p.add_argument("--count", type=int, default=1200)
    p.add_argument("--out")
    p.add_argument(
        "--workload",
        help="JSON array of questions in arrival order; use a test instance for cold measurements",
    )
    a = p.parse_args()
    if a.count <= 0 or a.rps <= 0:
        p.error("count and rps must be positive")
    raise SystemExit(0 if asyncio.run(run(a)) else 1)
