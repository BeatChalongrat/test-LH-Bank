import sqlalchemy as sa
import pytest

from knowledge.db import (
    accounts,
    case_activity,
    department_members,
    documents,
    engine_for,
    initialize,
    pdf_epoch,
    tickets,
)
from knowledge.hr_demo import ACCOUNTS, DEPARTMENT, DOC_ID, seed


def test_first_setup_retry_and_membership_drift(tmp_path):
    engine = engine_for("sqlite:///" + (tmp_path / "local.db").as_posix())
    initialize(engine)
    seed(engine)
    with engine.connect() as conn:
        keys = dict(
            conn.execute(sa.select(accounts.c.subject, accounts.c.token_hash)).all()
        )
        assert keys.keys() == ACCOUNTS.keys()
        epoch = conn.scalar(sa.select(pdf_epoch.c.value))
    with engine.begin() as conn:
        states = dict(conn.execute(sa.select(tickets.c.state, tickets.c.id)).all())
        assert states.keys() == {"open", "in_progress", "resolved"}
        conn.execute(
            tickets.update()
            .where(tickets.c.id == states["open"])
            .values(state="resolved", resolution="ตรวจครบแล้ว")
        )
    seed(engine)
    with engine.begin() as conn:
        assert conn.scalar(sa.select(pdf_epoch.c.value)) == epoch
        assert conn.scalar(sa.select(sa.func.count()).select_from(tickets)) == 3
        assert conn.scalar(sa.select(sa.func.count()).select_from(case_activity)) == 3
        assert (
            conn.scalar(
                sa.select(tickets.c.resolution).where(tickets.c.id == states["open"])
            )
            == "ตรวจครบแล้ว"
        )
        assert keys == dict(
            conn.execute(sa.select(accounts.c.subject, accounts.c.token_hash)).all()
        )
        assert conn.execute(sa.select(documents.c.id, documents.c.role_mask)).all() == [
            (DOC_ID, 0)
        ]
        assert conn.execute(sa.select(department_members)).all() == [
            (DEPARTMENT, "local/demo.hr")
        ]
        conn.execute(
            department_members.insert().values(
                department=DEPARTMENT, subject="local/demo.non-hr"
            )
        )
    with pytest.raises(ValueError, match="already a member"):
        seed(engine)
    engine.dispose()
