import csv
import hashlib
import io
import json
import re
import subprocess
import tempfile
from pathlib import Path

import sqlalchemy as sa
from pypdf import PdfReader


from .db import LOCAL_EMBEDDING_SPACE, chunks, insert_for, settings


def ocr_page(path, number):
    """Extract Thai and English from a scanned page; low-quality OCR requires owner review."""
    with tempfile.TemporaryDirectory() as directory:
        prefix = Path(directory) / "page"
        subprocess.run(
            [
                "pdftoppm",
                "-f",
                str(number),
                "-l",
                str(number),
                "-singlefile",
                "-scale-to",
                "2500",
                "-png",
                str(path),
                str(prefix),
            ],
            check=True,
            timeout=90,
            capture_output=True,
        )
        result = subprocess.run(
            [
                "tesseract",
                str(prefix.with_suffix(".png")),
                "stdout",
                "-l",
                "tha+eng",
                "tsv",
            ],
            check=True,
            timeout=90,
            capture_output=True,
        )
    words = [
        w
        for w in csv.DictReader(
            io.StringIO(result.stdout.decode("utf-8")), delimiter="\t"
        )
        if w["level"] == "5" and w["text"].strip()
    ]
    if not words or sum(float(w["conf"]) for w in words) / len(words) < 80:
        raise ValueError(
            f"{path.name} page {number}: OCR requires document-owner review"
        )
    return " ".join(w["text"] for w in words)


def sections(path, max_pdf_pages=None):
    if path.suffix.lower() == ".pdf":
        reader = PdfReader(path)
        if reader.is_encrypted:
            raise ValueError("encrypted_pdf")
        if max_pdf_pages is not None and not 1 <= len(reader.pages) <= max_pdf_pages:
            raise ValueError("page_limit")
        for number, page in enumerate(reader.pages, 1):
            content = page.extract_text() or ""
            if len(content.strip()) < 20 or any(
                mark in content for mark in ("\x00", "\ufffd")
            ):
                content = ocr_page(path, number)
            yield f"หน้า {number}", content
    elif path.suffix.lower() in {".txt", ".md"}:
        yield "ข้อความ", path.read_text(encoding="utf-8-sig")
    else:
        raise ValueError(f"Unsupported file: {path.name}")


def split_blocks(text, limit=640):
    text = re.sub(r"[ \t]+", " ", text).strip()
    while len(text) > limit:
        stop = max(
            text.rfind("\n", limit // 2, limit), text.rfind(" ", limit // 2, limit)
        )
        stop = limit if stop < 0 else stop
        yield text[:stop].strip()
        text = text[max(1, stop - 60) :].strip()
    if text:
        yield text


def check_space(conn, encoder, initialize=False):
    space = getattr(encoder, "space", LOCAL_EMBEDDING_SPACE)
    stored = conn.scalar(
        sa.select(settings.c.value).where(settings.c.name == "embedding_space")
    )
    if stored is None:
        if conn.scalar(sa.select(chunks.c.ref).limit(1)):
            stored = LOCAL_EMBEDDING_SPACE
        if initialize:
            stmt = insert_for(conn, settings).values(
                name="embedding_space", value=stored or space
            )
            conn.execute(stmt.on_conflict_do_nothing(index_elements=["name"]))
            stored = conn.scalar(
                sa.select(settings.c.value).where(settings.c.name == "embedding_space")
            )
    if (stored or LOCAL_EMBEDDING_SPACE) != space:
        raise ValueError(
            "Embedding model differs from the index. Use a separate database or run reembed in maintenance."
        )


def ingest(engine, encoder, catalog):
    catalog = Path(catalog).resolve()
    docs = json.loads(catalog.read_text(encoding="utf-8-sig"))
    rows, ids = [], set()
    digest = hashlib.sha256(b"multilingual-e5-small-761b726:chunks-v1")
    for doc in docs:
        if doc["audience"] not in {"staff", "executive"} or doc["type"] not in {
            "policy",
            "chat",
        }:
            raise ValueError("Unknown document audience or type")
        if doc["id"] in ids or not re.fullmatch(r"[A-Z0-9-]+", doc["id"]):
            raise ValueError("Invalid or duplicate document ID")
        ids.add(doc["id"])
        path = (catalog.parent / doc["file"]).resolve()
        if not path.is_relative_to(catalog.parent):
            raise ValueError("File outside corpus")
        start = len(rows)
        for label, content in sections(path):
            for block in split_blocks(content):
                row = dict(
                    ref=f"{doc['id']}#{len(rows) - start + 1}",
                    doc_id=doc["id"],
                    title=doc["title"],
                    kind=doc["type"],
                    audience=doc["audience"],
                    version=doc["version"],
                    owner=doc["owner"],
                    location=label,
                    tags=doc.get("tags", ""),
                    body=block,
                )
                rows.append(row)
                digest.update(
                    json.dumps(row, sort_keys=True, ensure_ascii=False).encode()
                )
        if len(rows) == start:
            raise ValueError(f"Empty document: {path.name}")
    if not rows:
        raise ValueError("Empty catalog")
    release = digest.hexdigest()[:24]
    with engine.begin() as conn:
        check_space(conn, encoder, initialize=True)
        existing = conn.scalar(
            sa.select(sa.func.count())
            .select_from(chunks)
            .where(chunks.c.revision == release)
        )
        if existing == len(rows):
            activate(conn, release)
            return {"revision": release, "documents": len(ids), "chunks": len(rows)}
    embed_rows(encoder, rows, release, engine.dialect.name)
    with engine.begin() as conn:
        check_space(conn, encoder)
        statement = insert_for(conn, chunks).on_conflict_do_nothing(
            index_elements=["revision", "ref"]
        )
        conn.execute(statement, rows)
        activate(conn, release)
    return {"revision": release, "documents": len(ids), "chunks": len(rows)}


def embed_rows(encoder, rows, release, dialect):
    for i in range(0, len(rows), 12):
        batch = rows[i : i + 12]
        vectors = encoder.encode(
            [f"{r['title']} {r['tags']}\n{r['body']}" for r in batch], "passage"
        )
        for row, vec in zip(batch, vectors, strict=True):
            row.update(
                revision=release,
                embedding=vec.tobytes() if dialect == "sqlite" else vec,
            )


def activate(conn, release):
    stmt = insert_for(conn, settings).values(name="revision", value=release)
    conn.execute(
        stmt.on_conflict_do_update(index_elements=["name"], set_={"value": release})
    )


def reembed(engine, encoder, profile_id=None):
    """Offline, atomic rebuild of catalog history and uploaded chunks in their existing ACLs."""
    from .documents import bump_epoch

    with engine.begin() as conn:
        if conn.dialect.name == "postgresql":
            conn.execute(sa.text("LOCK TABLE chunks IN EXCLUSIVE MODE"))
        last = None
        count = 0
        while True:
            stmt = sa.select(
                chunks.c.revision,
                chunks.c.ref,
                chunks.c.title,
                chunks.c.tags,
                chunks.c.body,
            )
            if last:
                stmt = stmt.where(sa.tuple_(chunks.c.revision, chunks.c.ref) > last)
            rows = (
                conn.execute(stmt.order_by(chunks.c.revision, chunks.c.ref).limit(12))
                .mappings()
                .all()
            )
            if not rows:
                break
            vectors = encoder.encode(
                [f"{r['title']} {r['tags']}\n{r['body']}" for r in rows], "passage"
            )
            for row, vector in zip(rows, vectors, strict=True):
                conn.execute(
                    chunks.update()
                    .where(
                        chunks.c.revision == row["revision"], chunks.c.ref == row["ref"]
                    )
                    .values(
                        embedding=vector.tobytes()
                        if conn.dialect.name == "sqlite"
                        else vector
                    )
                )
            last = (rows[-1]["revision"], rows[-1]["ref"])
            count += len(rows)
        space = getattr(encoder, "space", LOCAL_EMBEDDING_SPACE)
        stmt = insert_for(conn, settings).values(name="embedding_space", value=space)
        conn.execute(
            stmt.on_conflict_do_update(index_elements=["name"], set_={"value": space})
        )
        if profile_id:
            from .db import provider_routes

            conn.execute(
                provider_routes.update()
                .where(provider_routes.c.purpose == "embedding")
                .values(
                    profile_id=profile_id,
                    version=provider_routes.c.version + 1,
                    actor="system/reembed",
                )
            )
        bump_epoch(conn)
    return {"chunks_reembedded": count}
