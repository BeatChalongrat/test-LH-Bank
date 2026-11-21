"""Opt-in local acceptance fixture. Uploaded documents use the normal indexing queue."""

import os
import secrets
import time

import sqlalchemy as sa

from .db import (
    ROOT,
    accounts,
    department_members,
    departments,
    documents,
    engine_for,
    initialize,
    insert_for,
    pdf_files,
)
from .documents import Upload, bump_epoch, enqueue_document, read_grants
from .web_auth import digest

DOC_ID = "DOC-8BCE6DBEFBDA42DC"
DEPARTMENT = "HR-DEMO"
ACCOUNTS = {"demo.hr": "employee", "demo.non-hr": "employee", "demo.admin": "admin"}


def seed(engine):
    if os.getenv("LOCAL_AUTH", "1") != "1" or os.getenv("OIDC_ISSUER"):
        raise ValueError("Use an isolated local installation with invitations enabled")
    file = ROOT / "data/hr-demo/orchid-health-policy.pdf"
    content = file.read_bytes()
    with engine.begin() as conn:
        if conn.dialect.name == "postgresql":
            conn.execute(
                sa.text("SELECT pg_advisory_xact_lock(hashtext('hr-demo-setup'))")
            )
        for name, role in ACCOUNTS.items():
            conn.execute(
                insert_for(conn, accounts)
                .values(
                    subject=name,
                    role=role,
                    token_hash=digest(secrets.token_urlsafe(32)),
                )
                .on_conflict_do_nothing(index_elements=["subject"])
            )
            if (
                conn.scalar(
                    sa.select(accounts.c.role).where(accounts.c.subject == name)
                )
                != role
            ):
                raise ValueError(f"Account already has a different role: {name}")
        conn.execute(
            insert_for(conn, departments)
            .values(code=DEPARTMENT, name="HR (ทดสอบ)")
            .on_conflict_do_nothing(index_elements=["code"])
        )
        if conn.scalar(
            sa.select(department_members.c.department).where(
                department_members.c.department == DEPARTMENT,
                department_members.c.subject == "local/demo.non-hr",
            )
        ):
            raise ValueError(
                "demo.non-hr is already a member of HR-DEMO; review its membership"
            )
        member = conn.execute(
            insert_for(conn, department_members)
            .values(department=DEPARTMENT, subject="local/demo.hr")
            .on_conflict_do_nothing(index_elements=["department", "subject"])
        )
        if member.rowcount:
            bump_epoch(conn)
        existing = (
            conn.execute(sa.select(documents).where(documents.c.id == DOC_ID))
            .mappings()
            .first()
        )
        if existing:
            grants = read_grants(conn, [DOC_ID])[DOC_ID]
            if (
                existing["role_mask"]
                or not existing["enabled"]
                or grants != [("department", DEPARTMENT)]
                or bytes(
                    conn.scalar(
                        sa.select(pdf_files.c.body).where(pdf_files.c.doc_id == DOC_ID)
                    )
                )
                != content
            ):
                raise ValueError(
                    "The existing fixture has different content or access; review it in Admin"
                )
        else:
            upload = Upload(
                title="ระเบียบเบิกค่าตรวจสุขภาพออร์คิด (ทดสอบสิทธิ์ HR)",
                owner="ฝ่ายทรัพยากรบุคคล (จำลอง)",
                filename=file.name,
                departments={DEPARTMENT},
            )
            enqueue_document(conn, DOC_ID, upload, content, "system/local-demo")


def main():
    engine = engine_for()
    initialize(engine)
    seed(engine)
    for _ in range(120):
        with engine.connect() as conn:
            row = conn.execute(
                sa.select(documents.c.status, documents.c.error).where(
                    documents.c.id == DOC_ID
                )
            ).one()
        if row.status == "ready":
            print(f"{DOC_ID} ready; HR-DEMO department only")
            return
        if row.status == "failed":
            raise SystemExit(f"Indexing failed: {row.error}")
        time.sleep(1)
    raise SystemExit(
        "Indexing still pending. Check docker compose logs indexer and rerun setup."
    )


if __name__ == "__main__":
    main()
