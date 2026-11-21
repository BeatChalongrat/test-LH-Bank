"""Role-scoped library, case ownership and employee workspace queries."""

import time
import uuid
from typing import Literal

import sqlalchemy as sa
from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from .db import (
    MANAGED_REVISION,
    assignments,
    base_revision,
    case_activity,
    case_documents,
    chunks,
    documents,
    managed_access,
    set_scope,
    tickets,
    visible_chunks,
)
from .security import redact


class Resolution(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    resolution: str = Field(min_length=3, max_length=2000)


def review_scope(role):
    return {"reviewer": "staff", "executive": "executive"}.get(role)


def readable_cases(user):
    own = tickets.c.subject == user.subject
    if user.role != "executive":
        own = sa.and_(own, tickets.c.scope == "staff")
    scope = review_scope(user.role)
    return sa.and_(
        sa.or_(own, tickets.c.scope == scope) if scope else own, case_clearance(user)
    )


def case_clearance(user):
    allowed = sa.select(documents.c.id).where(
        managed_access(documents.c.id, user.role, user.subject)
    )
    return ~sa.exists().where(
        case_documents.c.ticket_id == tickets.c.id,
        case_documents.c.doc_id.not_in(allowed),
    )


def install_workspace(app, current):
    def record(conn, ticket_id, user, action):
        conn.execute(
            case_activity.insert().values(
                id=uuid.uuid4().hex,
                ticket_id=ticket_id,
                actor=user.subject,
                action=action,
                ts=time.time(),
            )
        )

    @app.get("/api/workspace")
    def overview(user=Depends(current)):
        with app.state.service.engine.connect() as conn:
            set_scope(conn, user.role, user.subject)
            documents = conn.scalar(
                sa.select(sa.func.count(sa.distinct(chunks.c.doc_id))).where(
                    visible_chunks(user.role, base_revision(), user.subject)
                )
            )
            states = dict(
                conn.execute(
                    sa.select(tickets.c.state, sa.func.count())
                    .where(readable_cases(user))
                    .group_by(tickets.c.state)
                ).all()
            )
        return {
            "documents": documents,
            "open": states.get("open", 0),
            "in_progress": states.get("in_progress", 0),
            "resolved": states.get("resolved", 0),
        }

    @app.get("/api/sources")
    def sources(
        user=Depends(current),
        offset: int = Query(0, ge=0),
        limit: int = Query(50, ge=1, le=200),
        q: str = Query("", max_length=160),
        kind: Literal["policy", "chat"] | None = None,
    ):
        with app.state.service.engine.connect() as conn:
            set_scope(conn, user.role, user.subject)
            fields = [
                chunks.c[k]
                for k in (
                    "ref",
                    "title",
                    "doc_id",
                    "kind",
                    "location",
                    "version",
                    "owner",
                    "body",
                )
            ]
            stmt = sa.select(
                *fields, (chunks.c.revision == MANAGED_REVISION).label("original")
            ).where(
                visible_chunks(user.role, base_revision(), user.subject),
            )
            if q.strip():
                stmt = stmt.where(
                    sa.or_(
                        *(
                            col.icontains(q.strip(), autoescape=True)
                            for col in (
                                chunks.c.doc_id,
                                chunks.c.title,
                                chunks.c.owner,
                                chunks.c.body,
                            )
                        )
                    )
                )
            if kind:
                stmt = stmt.where(chunks.c.kind == kind)
            return list(
                conn.execute(
                    stmt.order_by(chunks.c.doc_id, chunks.c.ref)
                    .offset(offset)
                    .limit(limit)
                ).mappings()
            )

    @app.get("/api/tickets")
    def queue(
        user=Depends(current),
        offset: int = Query(0, ge=0),
        limit: int = Query(50, ge=1, le=200),
        state: Literal["open", "in_progress", "resolved"] | None = None,
    ):
        stmt = (
            sa.select(tickets, assignments.c.owner, assignments.c.claimed_at)
            .select_from(
                tickets.outerjoin(assignments, assignments.c.ticket_id == tickets.c.id)
            )
            .where(readable_cases(user))
        )
        if state:
            stmt = stmt.where(tickets.c.state == state)
        with app.state.service.engine.connect() as conn:
            return list(
                conn.execute(
                    stmt.order_by(tickets.c.created.desc(), tickets.c.id)
                    .offset(offset)
                    .limit(limit)
                ).mappings()
            )

    def writable(ticket_id, user):
        scope = review_scope(user.role)
        if not scope:
            raise HTTPException(403)
        return sa.and_(
            tickets.c.id == ticket_id, tickets.c.scope == scope, case_clearance(user)
        )

    @app.get("/api/tickets/{ticket_id}/activity")
    def activity(ticket_id: str, user=Depends(current)):
        with app.state.service.engine.connect() as conn:
            item = (
                conn.execute(
                    sa.select(tickets).where(
                        tickets.c.id == ticket_id, readable_cases(user)
                    )
                )
                .mappings()
                .first()
            )
            if not item:
                raise HTTPException(404)
            rows = [
                dict(r)
                for r in conn.execute(
                    sa.select(
                        case_activity.c.action,
                        case_activity.c.actor,
                        case_activity.c.ts,
                    )
                    .where(case_activity.c.ticket_id == ticket_id)
                    .order_by(case_activity.c.ts, case_activity.c.id)
                ).mappings()
            ]
            # Closed cases from older schemas already carry their original resolution metadata.
            if item["resolved_at"] and not any(
                row["action"] == "resolved" for row in rows
            ):
                rows.append(
                    {
                        "action": "resolved",
                        "actor": item["resolved_by"],
                        "ts": item["resolved_at"],
                    }
                )
            return [
                {"action": "opened", "actor": item["subject"], "ts": item["created"]},
                *rows,
            ]

    def conflict(conn, allowed):
        raise HTTPException(
            409 if conn.scalar(sa.select(tickets.c.id).where(allowed)) else 404
        )

    @app.post("/api/tickets/{ticket_id}/claim")
    def claim(ticket_id: str, user=Depends(current)):
        allowed = writable(ticket_id, user)
        with app.state.service.engine.begin() as conn:
            updated = conn.execute(
                tickets.update()
                .where(allowed, tickets.c.state == "open")
                .values(state="in_progress")
            )
            if updated.rowcount:
                record(conn, ticket_id, user, "claimed")
                conn.execute(
                    assignments.insert().values(
                        ticket_id=ticket_id, owner=user.subject, claimed_at=time.time()
                    )
                )
            elif not conn.scalar(
                sa.select(assignments.c.owner)
                .join(tickets, tickets.c.id == assignments.c.ticket_id)
                .where(
                    allowed,
                    tickets.c.state == "in_progress",
                    assignments.c.owner == user.subject,
                )
            ):
                conflict(conn, allowed)
        return {"state": "in_progress", "owner": user.subject}

    @app.post("/api/tickets/{ticket_id}/release")
    def release(ticket_id: str, user=Depends(current)):
        allowed = writable(ticket_id, user)
        owned = sa.and_(
            assignments.c.ticket_id == ticket_id, assignments.c.owner == user.subject
        )
        with app.state.service.engine.begin() as conn:
            updated = conn.execute(
                tickets.update()
                .where(
                    allowed, tickets.c.state == "in_progress", sa.exists().where(owned)
                )
                .values(state="open")
            )
            if not updated.rowcount:
                conflict(conn, allowed)
            conn.execute(assignments.delete().where(owned))
            record(conn, ticket_id, user, "released")
        return {"state": "open"}

    @app.post("/api/tickets/{ticket_id}/resolve")
    def resolve(ticket_id: str, body: Resolution, user=Depends(current)):
        allowed = writable(ticket_id, user)
        mine = sa.exists().where(
            assignments.c.ticket_id == tickets.c.id, assignments.c.owner == user.subject
        )
        actionable = sa.or_(
            tickets.c.state == "open", sa.and_(tickets.c.state == "in_progress", mine)
        )
        with app.state.service.engine.begin() as conn:
            updated = conn.execute(
                tickets.update()
                .where(allowed, actionable)
                .values(
                    state="resolved",
                    resolution=redact(body.resolution),
                    resolved_by=user.subject,
                    resolved_at=time.time(),
                )
            )
            if not updated.rowcount:
                conflict(conn, allowed)
            record(conn, ticket_id, user, "resolved")
        return {"state": "resolved"}
