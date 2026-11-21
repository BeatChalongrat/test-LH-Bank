"""Process the durable upload queue independently of web and inference workers."""

import json
import logging
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from uuid import uuid4

import sqlalchemy as sa

from .db import MANAGED_REVISION, chunks, documents, engine_for, pdf_files
from .documents import audit, bump_epoch
from .encoder import Encoder
from .ingest import embed_rows

log = logging.getLogger(__name__)
ERRORS = {
    "encrypted_pdf",
    "page_limit",
    "text_limit",
    "empty_pdf",
    "unreadable_pdf",
    "unreadable_file",
    "file_limit",
}
LEASE_SECONDS = 600


def claim(conn):
    now = time.time()
    eligible = sa.or_(
        documents.c.status == "queued",
        sa.and_(documents.c.status == "indexing", documents.c.lease_until <= now),
    )
    candidate = (
        sa.select(documents.c.id)
        .where(eligible)
        .order_by(documents.c.created, documents.c.id)
        .limit(1)
    )
    if conn.dialect.name == "postgresql":
        candidate = candidate.with_for_update(skip_locked=True)
    return (
        conn.execute(
            documents.update()
            .where(documents.c.id == candidate.scalar_subquery(), eligible)
            .values(
                status="indexing",
                lease=uuid4().hex,
                lease_until=now + LEASE_SECONDS,
                updated=now,
            )
            .returning(
                documents.c.id,
                documents.c.lease,
                documents.c.title,
                documents.c.owner,
                documents.c.filename,
            )
        )
        .mappings()
        .first()
    )


def extract(content, filename):
    with tempfile.TemporaryDirectory(prefix="knowledge-pdf-") as directory:
        path = Path(directory) / ("input" + Path(filename).suffix.lower())
        path.write_bytes(content)
        with subprocess.Popen(
            [sys.executable, "-m", "knowledge.pdf_extract", str(path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={
                key: os.environ[key]
                for key in (
                    "PATH",
                    "SYSTEMROOT",
                    "TEMP",
                    "TMP",
                    "LANG",
                    "LC_ALL",
                    "TESSDATA_PREFIX",
                )
                if key in os.environ
            },
            start_new_session=True,
        ) as process:
            try:
                output, error = process.communicate(timeout=300)
            except subprocess.TimeoutExpired:
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
                process.communicate()
                raise ValueError("processing_timeout") from None
        if process.returncode:
            code = error.decode("ascii", errors="ignore").strip().splitlines()[-1:]
            raise ValueError(
                code[0] if code and code[0] in ERRORS else "unreadable_file"
            )
        return json.loads(output)


def work_once(engine, encoder):
    with engine.begin() as conn:
        job = claim(conn)
    if job is None:
        return False
    doc_id = job["id"]
    owned = sa.and_(documents.c.id == doc_id, documents.c.lease == job["lease"])
    error = None
    rows = []
    try:
        with engine.connect() as conn:
            content = conn.scalar(
                sa.select(pdf_files.c.body).where(pdf_files.c.doc_id == doc_id)
            )
        rows = [
            dict(
                ref=f"{doc_id}#{number}",
                doc_id=doc_id,
                title=job["title"],
                kind="policy",
                audience="managed",
                version="1",
                owner=job["owner"],
                location=location,
                tags="",
                body=body,
            )
            for number, (location, body) in enumerate(
                extract(content, job["filename"]), 1
            )
        ]
        embed_rows(encoder, rows, MANAGED_REVISION, engine.dialect.name)
    except ValueError as exc:
        code = str(exc)
        error = code if code in ERRORS | {"processing_timeout"} else "index_failed"
    except sa.exc.SQLAlchemyError:
        raise
    except Exception:
        error = "index_failed"
    with engine.begin() as conn:
        state = conn.execute(
            documents.update()
            .where(owned)
            .values(
                status="failed" if error else "ready",
                error=error,
                updated=time.time(),
                lease=None,
                lease_until=None,
            )
            .returning(documents.c.role_mask, documents.c.enabled)
        ).first()
        if state is None:
            return True
        if not error:
            conn.execute(chunks.insert(), rows)
            bump_epoch(conn)
        audit(
            conn,
            doc_id,
            "failed" if error else "published",
            "system/indexer",
            state.role_mask,
            state.enabled,
        )
    if error:
        log.warning("document=%s outcome=%s", doc_id, error)
    else:
        log.info("document=%s outcome=ready chunks=%d", doc_id, len(rows))
    return True


def main():
    logging.basicConfig(level=logging.INFO)
    engine = engine_for()
    encoder = Encoder()
    try:
        while True:
            if not work_once(engine, encoder):
                time.sleep(2)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
