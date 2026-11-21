import argparse
import html
import json
import os
import time
from pathlib import Path

import sqlalchemy as sa

from .db import (
    MANAGED_REVISION,
    ROOT,
    STATE,
    accounts,
    cache,
    chunks,
    conversations,
    department_members,
    doc_grants,
    engine_for,
    events,
    initialize,
    invites,
    sessions,
    tickets,
)
from .documents import bump_epoch
from .encoder import Encoder
from .ingest import activate, ingest
from .security import ROLES, provision
from .web_auth import issue_invite


def main():
    p = argparse.ArgumentParser(prog="python -m knowledge")
    sub = p.add_subparsers(dest="action", required=True)
    for name in ("bootstrap", "ingest"):
        sub.add_parser(name).add_argument(
            "--catalog", type=Path, default=ROOT / "data/catalog.json"
        )
    sub.choices["bootstrap"].add_argument("--with-test-accounts", action="store_true")
    invite = sub.add_parser("issue-invite")
    invite.add_argument("name")
    invite.add_argument("role", choices=sorted(ROLES))
    sub.add_parser("revoke-sessions").add_argument("subject")
    create = sub.add_parser("issue-key")
    create.add_argument("name")
    create.add_argument("role", choices=sorted(ROLES))
    sub.add_parser("revoke-key").add_argument("name")
    sub.add_parser("activate").add_argument("revision")
    sub.add_parser("trace").add_argument("id")
    sub.add_parser("prune")
    args = p.parse_args()
    engine = engine_for()
    if args.action not in {"issue-invite", "revoke-sessions"}:
        initialize(engine)
    if args.action in {"bootstrap", "ingest"}:
        print(json.dumps(ingest(engine, Encoder(), args.catalog)))
        if (
            args.action == "bootstrap"
            and args.with_test_accounts
            and os.getenv("LOCAL_AUTH", "1") == "1"
        ):
            creds = STATE / "access.json"
            keys = json.loads(creds.read_text()) if creds.exists() else {}
            with engine.begin() as conn:
                for role in ("employee", "reviewer", "executive"):
                    if not conn.scalar(
                        sa.select(accounts.c.subject).where(accounts.c.subject == role)
                    ):
                        keys[role] = provision(conn, role, role)
                temp = creds.with_suffix(".tmp")
                temp.write_text(json.dumps(keys, indent=2), encoding="utf-8")
                temp.chmod(0o600)
                temp.replace(creds)
        if args.action == "bootstrap":
            base = os.getenv("LOCAL_URL", "http://127.0.0.1:8765").rstrip("/")
            # Older installations bookmarked this local file. Redirect to the portal without credentials.
            (STATE / "open.html").write_text(
                '<meta charset="utf-8"><meta name="referrer" content="no-referrer">'
                f'<meta http-equiv="refresh" content="0;url={html.escape(base, quote=True)}">'
                f'<a href="{html.escape(base, quote=True)}">เปิดศูนย์ความรู้พนักงาน</a>',
                encoding="utf8",
            )
    elif args.action == "issue-invite":
        with engine.begin() as conn:
            code = issue_invite(conn, args.name, args.role)
        print(code)
    elif args.action == "revoke-sessions":
        with engine.begin() as conn:
            print(
                "Revoked:",
                conn.execute(
                    sessions.delete().where(sessions.c.subject == args.subject)
                ).rowcount,
            )
    elif args.action == "issue-key":
        with engine.begin() as conn:
            key = provision(conn, args.name, args.role)
        print(key)
    elif args.action == "revoke-key":
        with engine.begin() as conn:
            count = conn.execute(
                accounts.delete().where(accounts.c.subject == args.name)
            ).rowcount
            subject = "local/" + args.name
            member = conn.execute(
                department_members.delete().where(
                    department_members.c.subject == subject
                )
            )
            grants = conn.execute(
                doc_grants.delete().where(
                    doc_grants.c.kind == "person", doc_grants.c.target == subject
                )
            )
            if member.rowcount or grants.rowcount:
                bump_epoch(conn)
            print("Revoked:", count)
    elif args.action == "activate":
        with engine.begin() as conn:
            if args.revision == MANAGED_REVISION or not conn.scalar(
                sa.select(chunks.c.ref)
                .where(chunks.c.revision == args.revision)
                .limit(1)
            ):
                raise SystemExit("Unknown revision")
            activate(conn, args.revision)
    elif args.action == "trace":
        with engine.connect() as conn:
            row = (
                conn.execute(sa.select(events).where(events.c.trace == args.id))
                .mappings()
                .first()
            )
            print(json.dumps(dict(row) if row else None, ensure_ascii=False, indent=2))
    elif args.action == "prune":
        with engine.begin() as conn:
            now = time.time()
            conn.execute(
                conversations.delete().where(conversations.c.updated < now - 30 * 86400)
            )
            for table in (sessions, invites):
                conn.execute(table.delete().where(table.c.expires <= now))
            conn.execute(cache.delete().where(cache.c.expires < now))
            conn.execute(events.delete().where(events.c.ts < now - 30 * 86400))
            conn.execute(
                tickets.delete().where(
                    tickets.c.state == "resolved",
                    tickets.c.resolved_at < now - 30 * 86400,
                )
            )


if __name__ == "__main__":
    main()
