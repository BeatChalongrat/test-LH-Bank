"""Department membership and account lookup for document administrators."""

import os
from typing import Annotated

import sqlalchemy as sa
from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, StringConstraints

from .db import accounts, department_members, departments, insert_for, sso_people

DepartmentCode = Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_-]{0,23}$")]
Subject = Annotated[str, StringConstraints(min_length=6, max_length=260)]


class Department(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    code: DepartmentCode
    name: Annotated[str, StringConstraints(min_length=2, max_length=80)]


class Membership(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject: Subject
    member: bool


def people_view():
    sso = sa.select(sso_people.c.subject, sso_people.c.label)
    if os.getenv("LOCAL_AUTH", "1") != "1":
        return sso.subquery()
    local = sa.select(
        (sa.literal("local/") + accounts.c.subject).label("subject"),
        accounts.c.subject.label("label"),
    )
    return local.union_all(sso).subquery()


def ensure_subjects(conn, subjects):
    if not subjects:
        return
    roster = people_view()
    known = set(
        conn.scalars(sa.select(roster.c.subject).where(roster.c.subject.in_(subjects)))
    )
    if known != set(subjects):
        raise HTTPException(422, "มีบัญชีที่ไม่อยู่ในรายชื่อปัจจุบัน")


def register_sso(conn, subject, label):
    stmt = insert_for(conn, sso_people).values(subject=subject, label=label)
    conn.execute(
        stmt.on_conflict_do_update(
            index_elements=["subject"],
            set_={"label": label},
            where=sso_people.c.label != label,
        )
    )


def install_directory(app, admin, bump_epoch):
    @app.get("/api/admin/people")
    def people(
        _=Depends(admin),
        q: str = Query("", max_length=260),
        department: DepartmentCode | None = None,
    ):
        roster = people_view()
        stmt = sa.select(
            roster, department_members.c.department.is_not(None).label("member")
        ).outerjoin(
            department_members,
            sa.and_(
                department_members.c.subject == roster.c.subject,
                department_members.c.department == department,
            ),
        )
        if q.strip():
            term = q.strip()
            stmt = stmt.where(
                sa.or_(
                    roster.c.label.icontains(term, autoescape=True),
                    roster.c.subject.icontains(term, autoescape=True),
                )
            )
        if department and not q.strip():
            stmt = stmt.where(department_members.c.department.is_not(None))
        with app.state.service.engine.connect() as conn:
            return list(
                conn.execute(
                    stmt.order_by(roster.c.label, roster.c.subject).limit(40)
                ).mappings()
            )

    @app.get("/api/admin/departments")
    def list_departments(_=Depends(admin)):
        with app.state.service.engine.connect() as conn:
            return list(
                conn.execute(
                    sa.select(departments).order_by(departments.c.name)
                ).mappings()
            )

    @app.post("/api/admin/departments", status_code=201)
    def create_department(dept: Department, _=Depends(admin)):
        with app.state.service.engine.begin() as conn:
            result = conn.execute(
                insert_for(conn, departments)
                .values(code=dept.code, name=dept.name)
                .on_conflict_do_nothing(index_elements=["code"])
            )
            if not result.rowcount:
                raise HTTPException(409, "รหัสแผนกนี้มีอยู่แล้ว")
        return dept

    @app.post("/api/admin/departments/{code}/membership")
    def membership(code: DepartmentCode, body: Membership, _=Depends(admin)):
        with app.state.service.engine.begin() as conn:
            exists = conn.scalar(
                sa.select(departments.c.code).where(departments.c.code == code)
            )
            if not exists:
                raise HTTPException(404)
            if body.member:
                ensure_subjects(conn, {body.subject})
                stmt = insert_for(conn, department_members).values(
                    department=code, subject=body.subject
                )
                stmt = stmt.on_conflict_do_nothing(
                    index_elements=["department", "subject"]
                )
            else:
                stmt = department_members.delete().where(
                    department_members.c.department == code,
                    department_members.c.subject == body.subject,
                )
            if conn.execute(stmt).rowcount:
                bump_epoch(conn)
        return {"subject": body.subject, "department": code, "member": body.member}
