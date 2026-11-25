import re

import numpy as np
import sqlalchemy as sa

from .db import chunks, revision_value, set_scope, visible_chunks
from .security import normalize


def grams(text):
    text = re.sub(r"\s+", "", normalize(text))
    return {text[i : i + 3] for i in range(max(1, len(text) - 2))}


def query_text(query):
    return normalize(query).replace("แท๊กซี่", "แท็กซี่").replace("แทกซี่", "แท็กซี่")


def retrieve(conn, encoder, query, role, revision, limit=3, subject="", vector=None):
    set_scope(conn, role, subject)
    query = query_text(query)
    case_ids = re.findall(r"(?:CS|HR|FIN)-\d{3,}\b", query, re.I)
    mentions_chat = bool(case_ids or re.search(r"ในแช[ตท]", query))
    asks_policy = re.search(
        r"ตาม(?:นโยบาย|ระเบียบ)|ระเบียบ(?:จริง|กำหนด|ว่า)|official policy|under (?:the )?policy",
        query,
        re.I,
    )
    kind = "chat" if mentions_chat and not asks_policy else "policy"
    vec = encoder.encode([query], "query")[0] if vector is None else vector
    stmt = sa.select(chunks).where(
        revision_value() == revision,
        visible_chunks(role, revision.split(":", 1)[0], subject),
        chunks.c.kind == kind,
    )
    if kind == "chat":
        for identifier in set(case_ids):
            stmt = stmt.where(chunks.c.tags.ilike("%" + identifier + "%"))
    if conn.dialect.name == "postgresql":
        conn.execute(sa.text("SET LOCAL hnsw.iterative_scan = strict_order"))
        stmt = stmt.order_by(chunks.c.embedding.cosine_distance(vec)).limit(40)
    tokens = grams(query)
    ranked = []
    for item in conn.execute(stmt).mappings():
        row = dict(item)
        embedding = row.pop("embedding")
        vector = (
            np.frombuffer(embedding, np.float32)
            if isinstance(embedding, bytes)
            else embedding
        )
        dense = float(np.dot(vec, vector))
        words = grams(" ".join([row["doc_id"], row["title"], row["tags"], row["body"]]))
        overlap = len(tokens & words) / max(1, len(tokens))
        # E5 cosine scores cluster near 0.8; combine a rescaled semantic signal and Thai trigrams.
        scaled = (
            dense
            if getattr(encoder, "space", "").startswith("api-")
            else (dense - 0.72) / 0.25
        )
        semantic = max(0.0, min(1.0, scaled))
        row.update(score=round(0.52 * semantic + 0.48 * overlap, 4), overlap=overlap)
        ranked.append(row)
    return sorted(ranked, key=lambda r: (-r["score"], r["ref"]))[:limit]
