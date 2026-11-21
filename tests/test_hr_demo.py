import sqlalchemy as sa
import pytest

from knowledge.db import accounts, department_members, documents, engine_for, initialize
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
    seed(engine)
    with engine.begin() as conn:
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
