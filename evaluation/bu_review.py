"""Score a BU-adjudicated JSONL export. Each row is one question at one ACL revision."""

import argparse
import json
import math
from pathlib import Path


def proportion(count, total):
    if not total:
        return {"numerator": count, "denominator": total, "rate": None, "ci95": None}
    rate = count / total
    z = 1.96
    scale = 1 + z**2 / total
    center = (rate + z**2 / (2 * total)) / scale
    radius = z * math.sqrt(rate * (1 - rate) / total + z**2 / (4 * total**2)) / scale
    return {
        "numerator": count,
        "denominator": total,
        "rate": rate,
        "ci95": [max(0, center - radius), min(1, center + radius)],
    }


def score(rows):
    if not rows:
        raise ValueError("Provide at least one adjudicated row")
    seen = set()
    answered = []
    for row in rows:
        if row["id"] in seen:
            raise ValueError(f"Duplicate case {row['id']}")
        seen.add(row["id"])
        if (
            type(row["correct"]) is not bool
            or type(row["access_violation"]) is not bool
        ):
            raise ValueError("Adjudicate correctness and access before scoring")
        if row["status"] not in {"answered", "fallback", "refused"}:
            raise ValueError("Unknown response status")
        if row["status"] == "answered":
            if type(row["unsupported_claim"]) is not bool:
                raise ValueError("Review all claims in every automatic answer")
            answered.append(row)
        for field in ("gold_visible_docs", "retrieved_docs"):
            if not isinstance(row[field], list) or not all(
                isinstance(doc, str) and doc for doc in row[field]
            ):
                raise ValueError(f"Expected a list of document IDs for {field}")
    gold = [row for row in rows if row["gold_visible_docs"]]
    metrics = {
        "decision_accuracy": proportion(sum(r["correct"] for r in rows), len(rows)),
        "answer_coverage": proportion(len(answered), len(rows)),
        "automatic_answer_accuracy": proportion(
            sum(r["correct"] for r in answered), len(answered)
        ),
        "hallucination_rate": proportion(
            sum(r["unsupported_claim"] for r in answered), len(answered)
        ),
        "access_violations": sum(r["access_violation"] for r in rows),
        "retrieval_questions": len(gold),
    }
    for k in (1, 3):
        precision, recall = [], []
        for row in gold:
            relevant = set(row["gold_visible_docs"])
            retrieved = list(dict.fromkeys(row["retrieved_docs"]))[:k]
            hits = len(relevant.intersection(retrieved))
            precision.append(hits / len(retrieved) if retrieved else 0)
            recall.append(hits / len(relevant))
        metrics[f"precision@{k}"] = sum(precision) / len(gold) if gold else None
        metrics[f"recall@{k}"] = sum(recall) / len(gold) if gold else None
    return metrics


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("reviews", type=Path)
    args = parser.parse_args()
    rows = [
        json.loads(line)
        for line in args.reviews.read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]
    print(json.dumps(score(rows), indent=2, allow_nan=False))
