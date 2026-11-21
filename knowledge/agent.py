"""Authenticated conversations, bounded tool routing and the AI operations console."""

import hashlib
import re
import time
from collections import Counter
from dataclasses import dataclass
from uuid import UUID, uuid4

import anyio
import sqlalchemy as sa
from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .db import (
    conversations,
    events,
    insert_for,
    revision,
    revision_value,
    tickets,
    tool_changes,
    tool_controls,
    turns,
)
from .security import INJECTION, OFF_TOPIC, normalize, redact
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


class ToolChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    version: int = Field(ge=1)


def allowed_threads(user):
    return sa.and_(
        conversations.c.subject == user.subject,
        conversations.c.role == user.role,
        conversations.c.revision == revision_value(),
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

    def complete(self, conn, result):
        now = time.time()
        updated = conn.execute(
            conversations.update()
            .where(
                conversations.c.id == self.conversation,
                conversations.c.lease == self.lease,
                conversations.c.revision == revision_value(),
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


def reserve(engine, user, chat_id, body):
    turn, parent, now = (
        str(body.turn_id),
        str(body.parent_id) if body.parent_id else None,
        time.time(),
    )
    fingerprint = hashlib.sha256(body.question.encode()).hexdigest()
    with engine.begin() as c:
        rev = revision(c)
        if not rev:
            raise HTTPException(503, "ดัชนียังไม่พร้อม")
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
            .values(lease=lease, lease_end=now + 120)
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


def install_agent(app, current, answer):
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

    @app.post("/api/conversations/{conversation_id}/messages")
    async def message(conversation_id: UUID, body: Message, user=Depends(current)):
        engine = app.state.service.engine
        pending = await anyio.to_thread.run_sync(
            reserve, engine, user, str(conversation_id), body
        )
        if isinstance(pending, dict):
            return pending
        try:
            tool, value, context = route(body.question, pending.previous)
            if tool == "case_status":
                return await anyio.to_thread.run_sync(
                    read_cases, engine, user, pending, value, context
                )

            def commit(conn, result):
                result.update(tool="knowledge", used_context=context)
                pending.complete(conn, result)

            return await answer(user, value, commit, body.question)
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

    @app.get("/api/admin/agent")
    def overview(_=Depends(admin)):
        service = app.state.service
        with service.engine.connect() as c:
            rev = revision(c)
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
            model=service.generator.model,
            model_revision=getattr(service.generator, "version", ""),
            protocol=getattr(service.generator, "protocol", ""),
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
