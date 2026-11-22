"""Private chat attachments and bounded, read-only tools."""

import csv
import io
import json
import math
import os
import re
import subprocess
import sys
import time
from xml.etree.ElementTree import ParseError
from openpyxl.utils.exceptions import InvalidFileException
from pathlib import Path
from uuid import UUID, uuid4
from zipfile import ZipFile, BadZipFile

import anyio
import sqlalchemy as sa
from fastapi import Depends, HTTPException, Request

from .db import chat_files, events, turn_artifacts
from .document_worker import extract
from .file_formats import FORMATS, MAX_XML
from .security import normalize

LIMIT = 5 * 1024 * 1024
AGE = 30 * 86400


def as_number(value):
    try:
        n = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return n if math.isfinite(n) and abs(n) <= 1e15 else None


def read_table(rows):
    table = []
    for row in rows:
        values = ["" if v is None else str(v) for v in row]
        if not any(v.strip() for v in values):
            continue
        if len(table) >= 501 or len(values) > 16 or any(len(v) > 1000 for v in values):
            raise ValueError(
                "รองรับ 500 แถวข้อมูล 16 คอลัมน์ · Up to 500 rows and 16 columns"
            )
        table.append(values)
    if len(table) < 2:
        raise ValueError(
            "ต้องมีหัวตารางและข้อมูลอย่างน้อย 1 แถว · Include a header and one data row"
        )
    headers = table.pop(0)
    if any(not v.strip() for v in headers) or len(set(headers)) != len(headers):
        raise ValueError("ตั้งชื่อคอลัมน์ให้ครบและไม่ซ้ำ · Use unique column names")
    if any(len(r) > len(headers) for r in table):
        raise ValueError("จำนวนคอลัมน์เกินหัวตาราง · Check the column count")
    return {
        "headers": headers,
        "rows": [r + [""] * (len(headers) - len(r)) for r in table],
    }


def parse_file(raw, name):
    ext = Path(name).suffix.lower()
    table = None
    if ext == ".csv":
        encoding = (
            "utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
        )
        table = read_table(csv.reader(io.StringIO(raw.decode(encoding))))
    elif ext == ".xlsx":
        from openpyxl import load_workbook

        with ZipFile(io.BytesIO(raw)) as archive:
            parts = archive.infolist()
            if len(parts) > 1000 or sum(p.file_size for p in parts) > MAX_XML:
                raise ValueError("Workbook exceeds size limit")
        wb = load_workbook(
            io.BytesIO(raw), read_only=True, data_only=True, keep_links=False
        )
        try:
            sheet = wb.worksheets[0]
            if (sheet.max_row or 0) > 501 or (sheet.max_column or 0) > 16:
                raise ValueError(
                    "รองรับ 500 แถวข้อมูล 16 คอลัมน์ · Up to 500 rows and 16 columns"
                )
            table = read_table(sheet.values)
        finally:
            wb.close()
    if table:
        blocks = [
            [f"Row {idx}", " | ".join(row)] for idx, row in enumerate(table["rows"], 2)
        ]
    else:
        blocks = extract(raw, name)
    if not blocks or sum(len(b[1]) for b in blocks) > 260000:
        raise ValueError("ไฟล์ว่างหรือข้อความยาวเกินไป · Empty or oversized file")
    return {"blocks": blocks, "table": table}


def own_file(engine, user, chat, fid):
    with engine.connect() as c:
        row = (
            c.execute(
                sa.select(chat_files).where(
                    chat_files.c.id == str(fid),
                    chat_files.c.chat == chat,
                    chat_files.c.subject == user.subject,
                    chat_files.c.role == user.role,
                    chat_files.c.created > time.time() - AGE,
                )
            )
            .mappings()
            .first()
        )
    if not row:
        raise HTTPException(404, "ไฟล์แนบหมดอายุหรือไม่มีสิทธิ์ · Attachment unavailable")
    return row


def install_files(app, current):
    @app.post("/api/conversations/{chat}/attachments", status_code=201)
    async def upload(chat: UUID, name: str, request: Request, user=Depends(current)):
        if (
            len(name) > 150
            or not name.strip()
            or any(ord(c) < 32 for c in name)
            or "/" in name
            or "\\" in name
            or Path(name).suffix.lower() not in FORMATS
        ):
            raise HTTPException(422, "ชนิดไฟล์หรือชื่อไฟล์ไม่รองรับ · Unsupported file")
        raw = bytearray()
        async for block in request.stream():
            if len(raw) + len(block) > LIMIT:
                raise HTTPException(413, "ไฟล์ต้องไม่เกิน 5 MB · File exceeds 5 MB")
            raw.extend(block)
        try:
            payload = await anyio.to_thread.run_sync(
                parse_file, bytes(raw), name, limiter=app.state.file_workers
            )
        except (
            ValueError,
            OSError,
            KeyError,
            IndexError,
            BadZipFile,
            ParseError,
            InvalidFileException,
            csv.Error,
        ) as exc:
            raise HTTPException(
                422, "อ่านไฟล์ไม่ได้ · Check file contents and size limits"
            ) from exc
        identity = uuid4().hex

        def save():
            with app.state.service.engine.begin() as c:
                recent = c.scalar(
                    sa.select(sa.func.count())
                    .select_from(chat_files)
                    .where(
                        chat_files.c.subject == user.subject,
                        chat_files.c.created > time.time() - 86400,
                    )
                )
                if recent >= 80:
                    raise HTTPException(
                        429, "ครบโควตาไฟล์รายวัน · Daily attachment limit reached"
                    )
                c.execute(
                    chat_files.insert().values(
                        id=identity,
                        chat=str(chat),
                        subject=user.subject,
                        role=user.role,
                        name=name,
                        created=time.time(),
                        payload=payload,
                    )
                )

        await anyio.to_thread.run_sync(save)
        return {"id": identity, "name": name, "tabular": payload["table"] is not None}


def numeric_columns(table):
    if not table:
        return {}
    result = {}
    for i, h in enumerate(table["headers"]):
        values = [as_number(r[i]) for r in table["rows"] if r[i].strip()]
        if values and all(v is not None for v in values):
            result[h] = values
    return result


def attachment_answer(question, file):
    blocks = file["payload"]["blocks"]
    q = normalize(question)
    if re.search(r"สรุป|summary|summari[sz]e|แสดง|preview", q, re.I):
        picks = blocks[:4]
    else:

        def grams(text):
            return {text[i : i + 3] for i in range(max(1, len(text) - 2))}

        terms = grams(q)
        ranked = sorted(
            ((len(terms & grams(normalize(b[1]))), i, b) for i, b in enumerate(blocks)),
            key=lambda x: (-x[0], x[1]),
        )
        picks = [b for score, _, b in ranked[:3] if score > 0]
    if not picks:
        return None
    return "\n\n".join(f"{loc}\n{text}" for loc, text in picks)


def private_result(engine, user, pending, tool, question, file, lang):
    stamp = time.time()
    table = file["payload"]["table"] if file else None
    extra = {}
    if tool == "calculator":
        expression = re.sub(
            r"^(?:คำนวณ|calculate|calc)\s*[:：]?\s*", "", question, flags=re.I
        )
        proc = subprocess.run(
            [sys.executable, "-I", str(Path(__file__).with_name("calculation.py"))],
            input=json.dumps(
                {"expression": expression, "columns": numeric_columns(table)}
            ),
            text=True,
            capture_output=True,
            timeout=4,
            env={k: os.environ[k] for k in ("SYSTEMROOT", "WINDIR") if k in os.environ},
        )
        if proc.returncode:
            raise HTTPException(
                422, 'สูตรไม่ถูกต้อง · Try (1200 + 300) / 3 or sum(col("Sales"))'
            )
        value = json.loads(proc.stdout)
        answer = f"{expression} = {value:,}"
        source = "เครื่องคิดเลข / Calculator" + (" · " + file["name"] if file else "")
    elif tool == "data":
        if not table:
            raise HTTPException(
                422, "แนบ CSV หรือ XLSX ก่อน · Attach a CSV or XLSX first"
            )
        cols = numeric_columns(table)
        answer = (
            f"{len(table['rows'])} rows · {len(table['headers'])} columns"
            if lang == "en"
            else f"พบ {len(table['rows']):,} แถว และ {len(table['headers'])} คอลัมน์"
        )
        if cols:
            answer += "\n" + " · ".join(
                f"Σ {h} = {sum(v):,.2f}" for h, v in cols.items()
            )
        extra = {"table": table, "metrics": list(cols)}
        source = file["name"]
    else:
        answer = attachment_answer(question, file)
        source = file["name"]
    result = dict(
        status="answered" if answer else "refused",
        reason="tool_result" if answer else "attachment_no_match",
        answer=answer
        or "ไม่พบข้อความที่ตรงกับคำถามในไฟล์นี้ · No matching passage in this file.",
        confidence=None,
        sources=[],
        attribution=source,
        policy_url=None,
        trace_id=uuid4().hex,
        ticket_id=None,
        revision=pending.revision,
        cached=False,
        tool=tool,
        used_context=False,
        **extra,
    )
    if file:
        result["attachment"] = {
            "id": file["id"],
            "name": file["name"],
            "tabular": bool(table),
        }
    return store_result(engine, user, pending, result, stamp)


def store_result(engine, user, pending, result, started, artifact=None):
    with engine.begin() as c:
        pending.complete(c, result)
        if artifact is not None:
            c.execute(
                turn_artifacts.insert().values(
                    conversation=pending.conversation,
                    turn_id=pending.turn,
                    format=result["artifact"]["format"],
                    content=artifact,
                )
            )
        c.execute(
            events.insert().values(
                trace=result["trace_id"],
                ts=time.time(),
                status=result["status"],
                reason=result["reason"],
                role=user.role,
                revision=pending.revision,
                duration_ms=(time.time() - started) * 1000,
                spans={"route": result["tool"]},
                hit=False,
                refs=[],
            )
        )
    return result
