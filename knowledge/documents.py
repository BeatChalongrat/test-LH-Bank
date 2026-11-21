"""Web-managed PDF uploads, publication metadata, and role-based access rules."""

import json
import time
from pathlib import PurePath
from typing import Literal
from urllib.parse import quote, unquote
from uuid import uuid4

import anyio
import sqlalchemy as sa
from fastapi import Depends, HTTPException, Query, Request, Response
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from .db import (
    ROLE_BITS,
    departments,
    doc_grants,
    document_events,
    documents,
    managed_access,
    pdf_epoch,
    pdf_files,
    set_scope,
)
from .directory import DepartmentCode, Subject, ensure_subjects, install_directory
from .file_formats import FORMATS, OFFICE

FILE_MAX_BYTES = 15 * 1024 * 1024
Roles = set[Literal["employee", "reviewer", "executive", "admin"]]
SUMMARY = tuple(
    documents.c[key]
    for key in (
        "id",
        "title",
        "owner",
        "filename",
        "role_mask",
        "enabled",
        "status",
        "version",
        "created",
        "updated",
        "actor",
        "error",
    )
)


class Readers(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    roles: Roles = Field(default_factory=set)
    people: set[Subject] = Field(default_factory=set, max_length=30)
    departments: set[DepartmentCode] = Field(default_factory=set, max_length=30)

    @model_validator(mode="after")
    def specified(self):
        if not (self.roles or self.people or self.departments):
            raise ValueError("Choose at least one reader")
        return self

    def grants(self):
        return {("person", target) for target in self.people} | {
            ("department", target) for target in self.departments
        }


class Upload(Readers):
    title: str = Field(min_length=2, max_length=160)
    owner: str = Field(min_length=2, max_length=100)
    filename: str = Field(min_length=4, max_length=180)

    @field_validator("title", "owner", "filename")
    @classmethod
    def label(cls, value):
        if any(ord(char) < 32 for char in value):
            raise ValueError("Invalid control character")
        return value


class Access(Readers):
    enabled: bool
    version: int = Field(ge=1)


def replace_grants(conn, doc_id, readers):
    ensure_subjects(conn, readers.people)
    if readers.departments:
        known = set(
            conn.scalars(
                sa.select(departments.c.code).where(
                    departments.c.code.in_(readers.departments)
                )
            )
        )
        if known != readers.departments:
            raise HTTPException(422, "มีแผนกที่ไม่อยู่ในรายชื่อปัจจุบัน")
    conn.execute(doc_grants.delete().where(doc_grants.c.doc_id == doc_id))
    rows = [
        dict(doc_id=doc_id, kind=kind, target=target)
        for kind, target in readers.grants()
    ]
    if rows:
        conn.execute(doc_grants.insert(), rows)


def read_grants(conn, ids):
    result = {doc_id: [] for doc_id in ids}
    if ids:
        for doc_id, kind, target in conn.execute(
            sa.select(doc_grants)
            .where(doc_grants.c.doc_id.in_(ids))
            .order_by(doc_grants.c.kind, doc_grants.c.target)
        ):
            result[doc_id].append((kind, target))
    return result


def bump_epoch(conn):
    conn.execute(
        pdf_epoch.update().where(pdf_epoch.c.id == 1).values(value=uuid4().hex)
    )


def audit(conn, doc_id, action, actor, mask, enabled):
    conn.execute(
        document_events.insert().values(
            id=uuid4().hex,
            doc_id=doc_id,
            action=action,
            actor=actor,
            at=time.time(),
            role_mask=mask,
            enabled=enabled,
        )
    )


def present(row, grants):
    data = dict(row)
    mask = data.pop("role_mask")
    data["roles"] = [role for role, bit in ROLE_BITS.items() if mask & bit]
    data["people"] = [target for kind, target in grants if kind == "person"]
    data["departments"] = [target for kind, target in grants if kind == "department"]
    return data


def enqueue_document(conn, doc_id, metadata, content, actor):
    mask = sum(ROLE_BITS[role] for role in metadata.roles)
    now = time.time()
    conn.execute(
        documents.insert().values(
            id=doc_id,
            title=metadata.title,
            owner=metadata.owner,
            filename=PurePath(metadata.filename.replace("\\", "/")).name,
            role_mask=mask,
            status="queued",
            enabled=True,
            version=1,
            created=now,
            updated=now,
            actor=actor,
        )
    )
    conn.execute(pdf_files.insert().values(doc_id=doc_id, body=content))
    replace_grants(conn, doc_id, metadata)
    audit(conn, doc_id, "upload", actor, mask, True)
    return {"id": doc_id, "status": "queued"}


def enqueue(engine, metadata, content, actor):
    with engine.begin() as conn:
        return enqueue_document(
            conn, "DOC-" + uuid4().hex[:16].upper(), metadata, content, actor
        )


def install_documents(app, current):
    def admin(user=Depends(current)):
        if user.role != "admin":
            raise HTTPException(403)
        return user

    install_directory(app, admin, bump_epoch)

    @app.get("/api/documents/{doc_id}/{download}")
    def original(
        doc_id: str, download: Literal["pdf", "original"], user=Depends(current)
    ):
        with app.state.service.engine.connect() as conn:
            set_scope(conn, user.role, user.subject)
            row = conn.execute(
                sa.select(pdf_files.c.body, documents.c.filename)
                .join(documents, documents.c.id == pdf_files.c.doc_id)
                .where(
                    pdf_files.c.doc_id == doc_id,
                    managed_access(pdf_files.c.doc_id, user.role, user.subject),
                )
            ).first()
        if row is None:
            raise HTTPException(404)
        suffix = PurePath(row.filename).suffix.lower()
        if download == "pdf" and suffix != ".pdf":
            raise HTTPException(404)
        attachment = 'attachment; filename="{}"'.format(doc_id + suffix)
        attachment += "; filename*=UTF-8''" + quote(row.filename, safe="")
        return Response(
            row.body,
            media_type=FORMATS[suffix],
            headers={"Content-Disposition": attachment},
        )

    @app.get("/api/admin/documents")
    def list_documents(_=Depends(admin), offset: int = Query(0, ge=0)):
        with app.state.service.engine.connect() as conn:
            rows = list(
                conn.execute(
                    sa.select(*SUMMARY)
                    .order_by(documents.c.created.desc(), documents.c.id)
                    .offset(offset)
                    .limit(31)
                ).mappings()
            )
            grants = read_grants(conn, [row["id"] for row in rows])
            return [present(row, grants[row["id"]]) for row in rows]

    @app.post("/api/admin/documents", status_code=202)
    async def upload(request: Request, user=Depends(admin)):
        raw = request.headers.get("x-document-metadata", "")
        try:
            if not raw or len(raw) > 6200:
                raise ValueError
            metadata = Upload.model_validate(json.loads(unquote(raw, errors="strict")))
            suffix = PurePath(metadata.filename).suffix.lower()
        except (ValidationError, ValueError):
            raise HTTPException(422, "กรุณาระบุชื่อเอกสาร เจ้าของเรื่อง และกลุ่มผู้อ่าน") from None
        if suffix not in FORMATS:
            raise HTTPException(
                415, "กรุณาเลือกไฟล์ PDF, DOCX, XLSX, PPTX, TXT, MD หรือ CSV"
            )
        length = request.headers.get("content-length", "")
        if length.isdigit() and int(length) > FILE_MAX_BYTES:
            raise HTTPException(413, "ไฟล์ต้องมีขนาดไม่เกิน 15 MB")
        content = bytearray()
        async for chunk in request.stream():
            if len(content) + len(chunk) > FILE_MAX_BYTES:
                raise HTTPException(413, "ไฟล์ต้องมีขนาดไม่เกิน 15 MB")
            content.extend(chunk)
        if (
            not content
            or (suffix == ".pdf" and not content.startswith(b"%PDF-"))
            or (suffix in OFFICE and not content.startswith(b"PK\x03\x04"))
        ):
            raise HTTPException(415, "ข้อมูลไฟล์ไม่ตรงกับชนิดที่เลือก")
        return await anyio.to_thread.run_sync(
            enqueue, app.state.service.engine, metadata, bytes(content), user.subject
        )

    @app.post("/api/admin/documents/{doc_id}/access")
    def update_access(doc_id: str, body: Access, user=Depends(admin)):
        mask = sum(ROLE_BITS[role] for role in body.roles)
        with app.state.service.engine.begin() as conn:
            row = (
                conn.execute(
                    documents.update()
                    .where(
                        documents.c.id == doc_id,
                        documents.c.version == body.version,
                        documents.c.status == "ready",
                    )
                    .values(
                        role_mask=mask,
                        enabled=body.enabled,
                        version=body.version + 1,
                        updated=time.time(),
                        actor=user.subject,
                    )
                    .returning(*SUMMARY)
                )
                .mappings()
                .first()
            )
            if row is None:
                if not conn.scalar(
                    sa.select(documents.c.id).where(documents.c.id == doc_id)
                ):
                    raise HTTPException(404)
                raise HTTPException(409, "รายการเปลี่ยนหรือยังไม่พร้อม กรุณารีเฟรช")
            replace_grants(conn, doc_id, body)
            bump_epoch(conn)
            audit(conn, doc_id, "access", user.subject, mask, body.enabled)
            return present(row, sorted(body.grants()))
