"""Authenticated conversations, bounded tool routing and the AI operations console."""

import asyncio
import json
import hashlib
import httpx
from typing import Literal
from threading import Event
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from uuid import UUID, uuid4

import anyio
import sqlalchemy as sa
from fastapi import Depends, HTTPException
from fastapi.responses import Response, StreamingResponse
from .chat_exports import FORMATS as EXPORTS, export_turn
from .chat_generation import stream_text
from .chat_tools import install_files, own_file, private_result, store_result
from .sheet_report import choose_plan, make_report, report_request
from .provider import ProviderCoolingDown
from pydantic import BaseModel, ConfigDict, Field

from .db import (
    conversations,
    events,
    insert_for,
    revision,
    revision_value,
    tickets,
    turn_artifacts,
    tool_changes,
    tool_controls,
    turns,
)
from .security import INJECTION, OFF_TOPIC, normalize, redact
from .provider_profiles import active_profile
from .response_policy import POLICY_REF, POLICY_URL
from .workspace import case_clearance

RETENTION = 30 * 86400
FOLLOWUP = re.compile(
    r"^(?:(?:แล้ว|ถ้าอย่างนั้น|กรณีนี้|เรื่องนี้|ตามระเบียบนี้|ในกรณีนั้น)\s*)?(?:ต้อง\s*)?(?:ยื่น|แนบ|แจ้ง|ส่ง|ใช้แบบฟอร์ม|ขออนุมัติ|ใช้ระบบ|ใครอนุมัติ|ผ่านระบบ|ใครเป็นคนอนุมัติ|ล่วงหน้า|เบิกได้เท่าไร)"
)
NEW_TOPIC = re.compile(
    r"ลาป่วย|ลาพักร้อน|ลาคลอด|ใบเสร็จหาย|ค่าแท็กซี่|ค่าเดินทาง|เงินเดือน|สวัสดิการ|CS-\d+", re.I
)
MY_CASES = re.compile(
    r"(?:ขอ(?:ดู|เช็ก|เช็ค)|ดู|เช็ก|เช็ค|ติดตาม|รายการ|สถานะ|สรุป)?\s*(?:สถานะ)?(?:เคส|งานส่งต่อ|คำร้อง|รายการส่งต่อ)(?:ของ)?(?:ฉัน|ผม|เรา|ดิฉัน)(?:มีอะไรบ้าง|อยู่ขั้นตอนไหน|ถึงไหนแล้ว|ทั้งหมด|ล่าสุด|บ้าง)?(?:ครับ|ค่ะ|คะ)?[ ?？]*|(?:show |list |check )?my (?:cases|tickets)[ ?.]*",
    re.I,
)
CASE = re.compile(
    r"(?:ขอ(?:ดู|เช็ก|เช็ค)\s*|ติดตาม\s*|ดู\s*)?(?:สถานะ\s*)?(?:เคส|คำร้อง|ticket|case)\s*(?:เลขที่\s*)?[#:]?\s*([a-f0-9]{32}|นี้)(?:อยู่ขั้นตอนไหน|ถึงไหนแล้ว|ล่าสุด)?(?:ครับ|ค่ะ|คะ)?[ ?？]*",
    re.I,
)
STATES = {"open": "รอรับเรื่อง", "in_progress": "กำลังดำเนินการ", "resolved": "ปิดรายการแล้ว"}


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    turn_id: UUID
    parent_id: UUID | None = None
    question: str = Field(min_length=2, max_length=1200)

    mode: Literal[
        "auto", "knowledge", "attachment", "data", "calculator", "draft", "case_status"
    ] = "auto"
    attachment_id: UUID | None = None


class ToolChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    version: int = Field(ge=1)


PRIVATE_REV = "private-chat-only"


def current_revision():
    return sa.or_(
        conversations.c.revision == revision_value(),
        conversations.c.revision == PRIVATE_REV,
    )


def allowed_threads(user):
    return sa.and_(
        conversations.c.subject == user.subject,
        conversations.c.role == user.role,
        current_revision(),
        conversations.c.updated >= time.time() - RETENTION,
    )


@dataclass
class PendingTurn:
    conversation: str
    turn: str
    parent: str | None
    question: str
    request_hash: str
    revision: str
    lease: str
    previous: dict
    steps: list = field(default_factory=list)

    def complete(self, conn, result):
        now = time.time()
        result["steps"] = list(self.steps)
        updated = conn.execute(
            conversations.update()
            .where(
                conversations.c.id == self.conversation,
                conversations.c.lease == self.lease,
                conversations.c.revision == self.revision,
                current_revision(),
            )
            .values(last_id=self.turn, lease=None, lease_end=0, updated=now)
        )
        if not updated.rowcount or result["revision"] != self.revision:
            raise HTTPException(409, "รุ่นเอกสารหรือบทสนทนาเปลี่ยน กรุณาเริ่มแชตใหม่")
        conn.execute(
            turns.insert().values(
                conversation=self.conversation,
                id=self.turn,
                parent=self.parent,
                question=redact(self.question),
                request_hash=self.request_hash,
                created=now,
                result=result,
            )
        )


CALC_REQUEST = re.compile(
    r"^(?:คำนวณ|calculate|calc)\s*[:：]?\s*(?=[0-9(+.-]|sum\(|mean\(|round\(|max\(|min\(|count\()",
    re.I,
)
DRAFT_REQUEST = re.compile(
    r"^(?:ช่วย)?(?:ร่าง|เขียน)(?:อีเมล|จดหมาย|ประกาศ|ข้อความ|รายงาน|วาระ)|^(?:please )?(?:draft|write) (?:a |an )?(?:work |meeting |team )?(?:email|memo|letter|agenda|report|announcement)",
    re.I,
)
EXPORT_REQUEST = re.compile(
    r"^(?:ช่วย|ขอ|กรุณา|please )?(?:แปลง|ทำ|ดาวน์โหลด|ส่งออก|บันทึก|export|save|download)\s*(?:คำตอบนี้|คำตอบก่อนหน้า|this|the answer)?\s*(?:เป็น|as|to)?\s*(?:ไฟล์)?\s*(pdf|pptx?|powerpoint|docx?|word|xlsx|excel|csv|markdown|md)\s*(?:ให้หน่อย|หน่อย|ด้วย|ครับ|ค่ะ)?[.! ]*$",
    re.I,
)


def text_intent(body):
    if body.mode != "auto":
        return body.mode
    if CALC_REQUEST.search(body.question):
        return "calculator"
    if body.parent_id and EXPORT_REQUEST.fullmatch(body.question):
        return "export"
    if not body.attachment_id and DRAFT_REQUEST.search(body.question):
        return "draft"
    return "auto"


def reserve(engine, user, chat_id, body):
    turn, parent, now = (
        str(body.turn_id),
        str(body.parent_id) if body.parent_id else None,
        time.time(),
    )
    identity = (
        body.question
        if body.mode == "auto" and body.attachment_id is None
        else json.dumps([body.question, body.mode, str(body.attachment_id)])
    )
    fingerprint = hashlib.sha256(identity.encode()).hexdigest()
    with engine.begin() as c:
        rev = revision(c)
        if not rev:
            if (
                text_intent(body) in {"auto", "knowledge"}
                and not INJECTION.search(normalize(body.question))
                and not OFF_TOPIC.search(normalize(body.question))
                and not body.attachment_id
                and not MY_CASES.fullmatch(normalize(body.question))
                and not CASE.fullmatch(normalize(body.question))
            ):
                raise HTTPException(
                    503,
                    "คลังยังไม่พร้อม ให้ผู้ดูแลเชื่อมโมเดลค้นเอกสาร · Connect document search in Admin",
                )
            rev = PRIVATE_REV
        lease = uuid4().hex
        if not parent:
            inserted = c.scalar(
                insert_for(c, conversations)
                .values(
                    id=chat_id,
                    subject=user.subject,
                    role=user.role,
                    revision=rev,
                    title=redact(body.question)[:90],
                    updated=now,
                    lease=lease,
                    lease_end=now + 120,
                )
                .on_conflict_do_nothing(index_elements=["id"])
                .returning(conversations.c.id)
            )
            if inserted:
                # We created the thread and its exclusive turn lease in one write.
                return PendingTurn(
                    chat_id, turn, parent, body.question, fingerprint, rev, lease, {}
                )
        owned = sa.and_(allowed_threads(user), conversations.c.id == chat_id)
        row = (
            c.execute(sa.select(conversations).where(owned).with_for_update())
            .mappings()
            .first()
        )
        if not row:
            raise HTTPException(404)
        existing = (
            c.execute(
                sa.select(turns).where(
                    turns.c.conversation == chat_id, turns.c.id == turn
                )
            )
            .mappings()
            .first()
        )
        if existing:
            if existing["request_hash"] != fingerprint or existing["parent"] != parent:
                raise HTTPException(409)
            return existing["result"]
        updated = c.execute(
            conversations.update()
            .where(
                owned,
                conversations.c.last_id == parent,
                conversations.c.lease_end <= now,
            )
            .values(revision=rev, lease=lease, lease_end=now + 120)
        )
        if not updated.rowcount:
            raise HTTPException(409, "กำลังประมวลผลหรือมีข้อความใหม่ในบทสนทนา")
        previous = (
            c.scalar(
                sa.select(turns.c.result).where(
                    turns.c.conversation == chat_id, turns.c.id == parent
                )
            )
            if parent
            else None
        )
        return PendingTurn(
            chat_id,
            turn,
            parent,
            body.question,
            fingerprint,
            rev,
            lease,
            previous or {},
        )


def route(q, previous):
    normalized = normalize(q)
    if INJECTION.search(normalized) or OFF_TOPIC.search(normalized):
        return "knowledge", q, False
    if MY_CASES.fullmatch(normalized):
        return "case_status", None, False
    case = CASE.fullmatch(normalized)
    if case:
        ref = (
            case[1]
            if case[1] != "นี้"
            else previous.get("ticket_id") or previous.get("case_ref")
        )
        return "case_status", ref or "unspecified", bool(ref and case[1] == "นี้")
    sources = previous.get("sources") or []
    if sources and FOLLOWUP.search(normalized) and not NEW_TOPIC.search(normalized):
        return "knowledge", sources[0]["title"] + " — " + q, True
    return "knowledge", q, False


def read_cases(engine, user, pending, ref, used_context):
    start = time.perf_counter()
    with engine.begin() as c:
        enabled = c.scalar(
            sa.select(tool_controls.c.enabled).where(
                tool_controls.c.name == "case_status"
            )
        )
        own = sa.and_(tickets.c.subject == user.subject, case_clearance(user))
        if user.role != "executive":
            own = sa.and_(own, tickets.c.scope == "staff")
        status, reason, source = "answered", "tool_result", "ฐานงานสนับสนุน / เคสของฉัน"
        if not enabled:
            status, reason, source = "refused", "tool_disabled", "ระบบ / การตั้งค่าเครื่องมือ"
            answer = "ขณะนี้ปิดเครื่องมือตรวจเคสในแชต คุณยังเปิดเมนูงานสนับสนุนได้"
        elif ref == "unspecified":
            status, reason, source = "refused", "case_id_required", "ระบบ / การระบุเคส"
            answer = "กรุณาระบุเลขเคสจากเมนูงานสนับสนุน หรือพิมพ์ ดูเคสของฉัน เพื่อดูยอดรวม"
        elif ref:
            item = c.execute(
                sa.select(tickets.c.id, tickets.c.state).where(own, tickets.c.id == ref)
            ).first()
            if item:
                answer = f"เคส {item.id} มีสถานะ {STATES[item.state]}"
                source += " / " + item.id
            else:
                status, reason, source = (
                    "refused",
                    "case_not_visible",
                    "ระบบ / สิทธิ์การอ่านเคส",
                )
                answer = "ไม่พบเคสที่บัญชีของคุณเปิดดูได้ตามเลขที่ระบุ"
        else:
            totals = dict(
                c.execute(
                    sa.select(tickets.c.state, sa.func.count())
                    .where(own)
                    .group_by(tickets.c.state)
                ).all()
            )
            answer = (
                "คุณมีเคสทั้งหมด "
                + str(sum(totals.values()))
                + " รายการ\n"
                + " · ".join(f"{v} {totals.get(k,0)}" for k, v in STATES.items())
            )
        trace = uuid4().hex
        result = dict(
            status=status,
            reason=reason,
            answer=answer,
            confidence=None,
            sources=[],
            attribution=source if status == "answered" else POLICY_REF,
            policy_url=None if status == "answered" else POLICY_URL,
            revision=pending.revision,
            trace_id=trace,
            ticket_id=None,
            cached=False,
            tool="case_status",
            used_context=used_context,
            read_at=time.time(),
            case_ref=ref if status == "answered" else None,
        )
        c.execute(
            events.insert().values(
                trace=trace,
                ts=time.time(),
                status=status,
                reason=reason,
                role=user.role,
                revision=pending.revision,
                duration_ms=(time.perf_counter() - start) * 1000,
                spans={"route": "case_status"},
                hit=False,
                refs=[],
            )
        )
        pending.complete(c, result)
        return result


def install_agent(app, current, answer, run_model):
    install_files(app, current)

    def generate(instruction, content, limit, emit, stopped):
        service = app.state.service
        with service.engine.connect() as c:
            gen = service.generator_for(active_profile(c, "chat"))
        pieces = []
        length = 0
        started = time.monotonic()
        try:
            for chunk in stream_text(gen, instruction, content, limit):
                if stopped.is_set() or time.monotonic() - started > 55:
                    raise HTTPException(503, "หยุดการสร้างข้อความ · Generation stopped")
                length += len(chunk)
                if length > 16000:
                    raise ValueError("Response too long")
                pieces.append(chunk)
                emit("delta", chunk)
            result = "".join(pieces).strip()
            if not result:
                raise ValueError("Empty response")
            return result
        except (ValueError, httpx.HTTPError, ProviderCoolingDown) as exc:
            raise HTTPException(
                503,
                "เขียนข้อความไม่ได้ กรุณาตรวจการเชื่อมต่อโมเดลแชต · Check the chat model connection",
            ) from exc

    def admin(user=Depends(current)):
        if user.role != "admin":
            raise HTTPException(403)
        return user

    @app.get("/api/conversations")
    def chats(user=Depends(current)):
        with app.state.service.engine.connect() as c:
            return list(
                c.execute(
                    sa.select(
                        conversations.c.id,
                        conversations.c.title,
                        conversations.c.updated,
                    )
                    .where(allowed_threads(user), conversations.c.last_id.is_not(None))
                    .order_by(conversations.c.updated.desc())
                    .limit(50)
                ).mappings()
            )

    @app.get("/api/conversations/{conversation_id}")
    def history(conversation_id: UUID, user=Depends(current)):
        cid = str(conversation_id)
        with app.state.service.engine.connect() as c:
            scope = sa.and_(allowed_threads(user), conversations.c.id == cid)
            if not c.scalar(sa.select(conversations.c.id).where(scope)):
                raise HTTPException(404)
            rows = list(
                c.execute(
                    sa.select(
                        turns.c.id, turns.c.question, turns.c.result, turns.c.created
                    )
                    .select_from(turns.join(conversations))
                    .where(scope)
                    .order_by(turns.c.created.desc(), turns.c.id.desc())
                    .limit(50)
                ).mappings()
            )
        return rows[::-1]

    async def run_message(conversation_id, body, user, emit, stopped):
        emit("stage", "access")
        engine = app.state.service.engine
        pending = await anyio.to_thread.run_sync(
            reserve, engine, user, str(conversation_id), body
        )
        if isinstance(pending, dict):
            return pending
        pending.steps.append("access")

        def stage(code):
            pending.steps.append(code)
            emit("stage", code)

        try:
            tool, value, context = route(body.question, pending.previous)
            blocked = INJECTION.search(normalize(body.question)) or OFF_TOPIC.search(
                normalize(body.question)
            )
            file = None
            if body.attachment_id and not blocked:
                stage("file")
                file = await anyio.to_thread.run_sync(
                    own_file, engine, user, str(conversation_id), body.attachment_id.hex
                )
            if not blocked:
                intent = text_intent(body)
                if intent != "auto":
                    tool = intent
                elif file:
                    tool = "data" if file["payload"]["table"] else "attachment"
            if blocked and pending.revision == PRIVATE_REV:
                result = dict(
                    status="refused",
                    reason="guardrail",
                    answer="คำขอนี้อยู่นอกขอบเขตผู้ช่วยงาน · Please ask a work-related question.",
                    confidence=None,
                    sources=[],
                    attribution=POLICY_REF,
                    policy_url=POLICY_URL,
                    trace_id=uuid4().hex,
                    ticket_id=None,
                    revision=pending.revision,
                    cached=False,
                    tool="knowledge",
                    used_context=False,
                )
                return await anyio.to_thread.run_sync(
                    store_result, engine, user, pending, result, time.time()
                )
            if tool == "data" and body.mode == "auto" and report_request(body.question):
                stage("plan")
                selection = await run_model(
                    choose_plan,
                    app.state.service,
                    body.question,
                    file["payload"]["table"],
                )
                if stopped.is_set():
                    raise HTTPException(409, "หยุดการทำรายงาน / Report cancelled")
                return await anyio.to_thread.run_sync(
                    make_report,
                    engine,
                    user,
                    pending,
                    file,
                    selection,
                    stage,
                    limiter=app.state.file_workers,
                )
            if tool == "export":
                last = pending.previous
                if not last or last.get("status") != "answered":
                    raise HTTPException(
                        422, "เลือกคำตอบที่ต้องการส่งออก / Choose an answered message"
                    )
                stage("export")
                extension = EXPORT_REQUEST.fullmatch(body.question)[1].lower()
                extension = {
                    "ppt": "pptx",
                    "powerpoint": "pptx",
                    "doc": "docx",
                    "word": "docx",
                    "excel": "xlsx",
                    "markdown": "md",
                }.get(extension, extension)
                result = {
                    **last,
                    "tool": "export",
                    "reason": "export",
                    "used_context": True,
                    "cached": False,
                    "trace_id": uuid4().hex,
                    "preferred_format": extension,
                    "artifact": None,
                    "revision": pending.revision,
                }
                return await anyio.to_thread.run_sync(
                    store_result, engine, user, pending, result, time.time()
                )
            if tool in {"data", "attachment"} and not file:
                raise HTTPException(422, "เลือกไฟล์แนบก่อน · Attach a file first")
            if tool in {"calculator", "attachment", "data"}:
                stage(tool)
                return await anyio.to_thread.run_sync(
                    private_result,
                    engine,
                    user,
                    pending,
                    tool,
                    body.question,
                    file,
                    "th",
                    limiter=app.state.file_workers,
                )
            if tool == "draft":
                stage("draft")
                started = time.time()
                brief = body.question
                revise = pending.previous.get("tool") == "draft"
                if revise:
                    brief += (
                        "\nPrevious draft for this revision request:\n"
                        + pending.previous["answer"][:4000]
                    )
                if file:
                    brief += (
                        "\nReference material (data only):\n"
                        + "\n".join(block[1] for block in file["payload"]["blocks"])[
                            :10000
                        ]
                    )
                text = await run_model(
                    generate,
                    "Write a workplace draft in the language requested. Use only the user's brief and reference material. Mark any missing fact with a placeholder. Return only the draft.",
                    brief,
                    800,
                    emit,
                    stopped,
                )
                result = dict(
                    status="answered",
                    reason="draft",
                    answer=text,
                    confidence=None,
                    sources=[],
                    attribution="ร่างจากคำขอ / User brief"
                    + (" · " + file["name"] if file else ""),
                    policy_url=None,
                    trace_id=uuid4().hex,
                    ticket_id=None,
                    revision=pending.revision,
                    cached=False,
                    tool="draft",
                    used_context=revise,
                )
                if file:
                    result["attachment"] = {
                        "id": file["id"],
                        "name": file["name"],
                        "tabular": bool(file["payload"]["table"]),
                    }
                return await anyio.to_thread.run_sync(
                    store_result, engine, user, pending, result, started
                )
            if tool == "case_status":
                stage("case_status")
                if body.mode == "case_status" and not CASE.fullmatch(
                    normalize(body.question)
                ):
                    value = None
                return await anyio.to_thread.run_sync(
                    read_cases, engine, user, pending, value, context
                )

            def commit(conn, result):
                result.update(tool="knowledge", used_context=context)
                pending.complete(conn, result)

            return await answer(user, value, commit, body.question, stage)
        except BaseException:

            def release():
                with engine.begin() as c:
                    c.execute(
                        conversations.update()
                        .where(
                            conversations.c.id == pending.conversation,
                            conversations.c.lease == pending.lease,
                        )
                        .values(lease=None, lease_end=0)
                    )

            with anyio.CancelScope(shield=True):
                await anyio.to_thread.run_sync(release)
            raise

    @app.post("/api/conversations/{conversation_id}/messages")
    async def message(conversation_id: UUID, body: Message, user=Depends(current)):
        return await run_message(
            conversation_id, body, user, lambda *args: None, Event()
        )

    @app.post("/api/conversations/{conversation_id}/stream")
    async def stream(conversation_id: UUID, body: Message, user=Depends(current)):
        async def frames():
            loop = asyncio.get_running_loop()
            queue = asyncio.Queue()
            stopped = Event()
            streamed = False

            def emit(kind, value):
                nonlocal streamed
                if kind == "delta":
                    streamed = True
                if not stopped.is_set():
                    loop.call_soon_threadsafe(
                        queue.put_nowait, {"event": kind, "value": value}
                    )

            async def produce():
                try:
                    result = await run_message(
                        conversation_id, body, user, emit, stopped
                    )
                    if not streamed:
                        for offset in range(0, len(result["answer"]), 96):
                            emit("delta", result["answer"][offset : offset + 96])
                    emit("result", result)
                except HTTPException as exc:
                    emit(
                        "error",
                        {
                            "status": exc.status_code,
                            "message": exc.detail or "Request failed",
                        },
                    )
                except Exception:
                    import logging

                    logging.getLogger(__name__).exception("Chat stream failed")
                    emit(
                        "error",
                        {
                            "status": 500,
                            "message": "ระบบประมวลผลไม่สำเร็จ · Processing failed",
                        },
                    )
                finally:
                    loop.call_soon_threadsafe(queue.put_nowait, None)

            task = asyncio.create_task(produce())
            try:
                while (frame := await queue.get()) is not None:
                    yield json.dumps(frame, ensure_ascii=False) + "\n"
            finally:
                stopped.set()
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        return StreamingResponse(
            frames(),
            media_type="application/x-ndjson",
            headers={"X-Accel-Buffering": "no"},
        )

    @app.post("/api/conversations/{conversation_id}/title")
    async def name_chat(conversation_id: UUID, user=Depends(current)):
        cid = str(conversation_id)
        scope = sa.and_(allowed_threads(user), conversations.c.id == cid)
        engine = app.state.service.engine

        def first():
            with engine.connect() as c:
                return c.execute(
                    sa.select(
                        conversations.c.title,
                        turns.c.question,
                        turns.c.result["status"].as_string().label("status"),
                    )
                    .select_from(turns.join(conversations))
                    .where(scope)
                    .order_by(turns.c.created)
                    .limit(1)
                ).first()

        row = await anyio.to_thread.run_sync(first)
        if not row:
            raise HTTPException(404)
        if row.status != "answered" or row.title != row.question[:90]:
            return {"title": row.title}
        try:
            title = await run_model(
                generate,
                "Name this workplace conversation in the same language as the user. Output a single short title, at most 8 words. No quotes or commentary.",
                row.question,
                40,
                lambda *args: None,
                Event(),
            )
            title = title.splitlines()[0].strip(' "#*')[:60] or row.title
        except HTTPException:
            return {"title": row.title}

        def save():
            with engine.begin() as c:
                c.execute(
                    conversations.update()
                    .where(scope, conversations.c.title == row.title)
                    .values(title=title)
                )
                current_title = c.scalar(sa.select(conversations.c.title).where(scope))
                if current_title is None:
                    raise HTTPException(404)
                return current_title

        return {"title": await anyio.to_thread.run_sync(save)}

    @app.get("/api/conversations/{conversation_id}/turns/{turn_id}/export/{fmt}")
    async def download(
        conversation_id: UUID, turn_id: UUID, fmt: str, user=Depends(current)
    ):
        if fmt not in EXPORTS:
            raise HTTPException(404)

        def render():
            with app.state.service.engine.connect() as c:
                stored = turns.outerjoin(
                    turn_artifacts,
                    sa.and_(
                        turn_artifacts.c.conversation == turns.c.conversation,
                        turn_artifacts.c.turn_id == turns.c.id,
                        turn_artifacts.c.format == fmt,
                    ),
                )
                row = c.execute(
                    sa.select(
                        turns.c.question, turns.c.result, turn_artifacts.c.content
                    )
                    .select_from(
                        stored.join(
                            conversations, turns.c.conversation == conversations.c.id
                        )
                    )
                    .where(
                        allowed_threads(user),
                        conversations.c.id == str(conversation_id),
                        turns.c.id == str(turn_id),
                    )
                ).first()
                if not row:
                    raise HTTPException(404)
                asset = row.result.get("artifact")
                if asset and asset["format"] == fmt:
                    if row.content is None:
                        raise HTTPException(404)
                    return bytes(row.content)
            return export_turn(
                fmt, row.result.get("report_title", row.question), row.result
            )

        data = await anyio.to_thread.run_sync(render, limiter=app.state.file_workers)
        return Response(
            data,
            media_type=EXPORTS[fmt],
            headers={
                "Content-Disposition": f'attachment; filename="answer-{turn_id}.{fmt}"'
            },
        )

    @app.get("/api/admin/agent")
    def overview(_=Depends(admin)):
        service = app.state.service
        with service.engine.connect() as c:
            rev = revision(c)
            profile = active_profile(c, "chat")
            rows = c.execute(
                sa.select(events.c.status, events.c.reason, sa.func.count())
                .where(events.c.ts >= time.time() - 86400)
                .group_by(events.c.status, events.c.reason)
            ).all()
            control = (
                c.execute(
                    sa.select(tool_controls).where(
                        tool_controls.c.name == "case_status"
                    )
                )
                .mappings()
                .one()
            )
            audit = list(
                c.execute(
                    sa.select(tool_changes).order_by(tool_changes.c.at.desc()).limit(10)
                ).mappings()
            )
        totals = Counter()
        for status, _, n in rows:
            totals[status] += n
        return dict(
            model=profile["model"] if profile else service.generator.model,
            model_revision=str(profile["version"])
            if profile
            else getattr(service.generator, "version", ""),
            protocol=profile["provider"]
            if profile
            else getattr(service.generator, "protocol", ""),
            threshold=service.threshold,
            revision=rev,
            counts=totals,
            reasons=[{"reason": reason, "count": n} for _, reason, n in rows],
            case_status=control,
            changes=audit,
        )

    @app.post("/api/admin/tools/case-status")
    def tool_setting(body: ToolChange, user=Depends(admin)):
        with app.state.service.engine.begin() as c:
            updated = c.execute(
                tool_controls.update()
                .where(
                    tool_controls.c.name == "case_status",
                    tool_controls.c.version == body.version,
                )
                .values(enabled=body.enabled, version=body.version + 1)
            )
            if not updated.rowcount:
                raise HTTPException(409)
            c.execute(
                tool_changes.insert().values(
                    id=uuid4().hex,
                    at=time.time(),
                    actor=user.subject,
                    tool="case_status",
                    enabled=body.enabled,
                )
            )
        return {"enabled": body.enabled, "version": body.version + 1}
