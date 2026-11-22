import os
from pathlib import Path

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as lite_insert

ROOT = Path(__file__).resolve().parents[1]
STATE = Path(os.getenv("STATE_DIR", ROOT / "var"))
meta = sa.MetaData()
C = sa.Column
T = sa.String
LOCAL_EMBEDDING_SPACE = "multilingual-e5-small-761b726"
MANAGED_REVISION = "managed-pdf-v1"
ROLE_BITS = {"employee": 1, "reviewer": 2, "executive": 4, "admin": 8}

accounts = sa.Table(
    "accounts",
    meta,
    C("subject", T, primary_key=True),
    C("role", T, nullable=False),
    C("token_hash", T, nullable=False, unique=True),
)
invites = sa.Table(
    "login_invites",
    meta,
    C("digest", T, primary_key=True),
    C("account_hash", T, nullable=False),
    C("expires", sa.Float, nullable=False, index=True),
)
sessions = sa.Table(
    "web_sessions",
    meta,
    C("digest", T, primary_key=True),
    C("subject", T, nullable=False, index=True),
    C("role", T, nullable=False),
    C("account_hash", T),
    C("expires", sa.Float, nullable=False, index=True),
)
settings = sa.Table(
    "settings", meta, C("name", T, primary_key=True), C("value", T, nullable=False)
)
provider_profiles = sa.Table(
    "model_provider_profiles",
    meta,
    C("id", T, primary_key=True),
    C("name", T, nullable=False),
    C("purpose", T, nullable=False),
    C("provider", T, nullable=False),
    C("endpoint", T, nullable=False),
    C("model", T, nullable=False),
    C("secret", T, nullable=False),
    C("version", sa.Integer, nullable=False),
    C("actor", T, nullable=False),
    C("updated", sa.Float, nullable=False),
)
provider_routes = sa.Table(
    "model_provider_routes",
    meta,
    C("purpose", T, primary_key=True),
    C("profile_id", T, sa.ForeignKey("model_provider_profiles.id")),
    C("version", sa.Integer, nullable=False),
    C("actor", T),
    C("updated", sa.Float),
)
pdf_epoch = sa.Table(
    "managed_pdf_epoch",
    meta,
    C("id", sa.Integer, primary_key=True),
    C("value", T, nullable=False),
)
conversations = sa.Table(
    "conversations",
    meta,
    C("id", T, primary_key=True),
    C("subject", T, nullable=False, index=True),
    C("role", T, nullable=False),
    C("revision", T, nullable=False),
    C("title", T, nullable=False),
    C("updated", sa.Float, nullable=False, index=True),
    C("last_id", T),
    C("lease", T),
    C("lease_end", sa.Float, nullable=False),
)
chat_files = sa.Table(
    "private_chat_files",
    meta,
    C("id", T, primary_key=True),
    C("chat", T, nullable=False, index=True),
    C("subject", T, nullable=False, index=True),
    C("role", T, nullable=False),
    C("name", T, nullable=False),
    C("created", sa.Float, nullable=False, index=True),
    C("payload", sa.JSON, nullable=False),
)
turns = sa.Table(
    "conversation_turns",
    meta,
    C(
        "conversation",
        T,
        sa.ForeignKey("conversations.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    C("id", T, primary_key=True),
    C("parent", T),
    C("question", T, nullable=False),
    C("request_hash", T, nullable=False),
    C("created", sa.Float, nullable=False),
    C("result", sa.JSON, nullable=False),
)
tool_controls = sa.Table(
    "tool_controls",
    meta,
    C("name", T, primary_key=True),
    C("enabled", sa.Boolean, nullable=False),
    C("version", sa.Integer, nullable=False),
)
tool_changes = sa.Table(
    "tool_changes",
    meta,
    C("id", T, primary_key=True),
    C("at", sa.Float, index=True, nullable=False),
    C("actor", T, nullable=False),
    C("tool", T, nullable=False),
    C("enabled", sa.Boolean, nullable=False),
)
documents = sa.Table(
    "managed_documents",
    meta,
    C("id", T, primary_key=True),
    C("title", T, nullable=False),
    C("owner", T, nullable=False),
    C("filename", T, nullable=False),
    C("role_mask", sa.Integer, nullable=False),
    C("enabled", sa.Boolean, nullable=False),
    C("status", T, nullable=False, index=True),
    C("version", sa.Integer, nullable=False),
    C("created", sa.Float, nullable=False, index=True),
    C("updated", sa.Float, nullable=False),
    C("actor", T, nullable=False),
    C("lease", T),
    C("lease_until", sa.Float),
    C("error", T),
)
departments = sa.Table(
    "departments",
    meta,
    C("code", T, primary_key=True),
    C("name", T, nullable=False),
)
department_members = sa.Table(
    "department_members",
    meta,
    C("department", T, sa.ForeignKey("departments.code"), primary_key=True),
    C("subject", T, primary_key=True, index=True),
)
sso_people = sa.Table(
    "sso_people",
    meta,
    C("subject", T, primary_key=True),
    C("label", T, nullable=False),
)
doc_grants = sa.Table(
    "managed_document_grants",
    meta,
    C("doc_id", T, sa.ForeignKey("managed_documents.id"), primary_key=True),
    C("kind", T, primary_key=True),
    C("target", T, primary_key=True),
    sa.CheckConstraint("kind IN ('person', 'department')"),
)
pdf_files = sa.Table(
    "managed_pdf_files",
    meta,
    C("doc_id", T, sa.ForeignKey("managed_documents.id"), primary_key=True),
    C("body", sa.LargeBinary, nullable=False),
)
document_events = sa.Table(
    "managed_document_events",
    meta,
    C("id", T, primary_key=True),
    C("doc_id", T, sa.ForeignKey("managed_documents.id"), nullable=False, index=True),
    C("action", T, nullable=False),
    C("actor", T, nullable=False),
    C("at", sa.Float, nullable=False),
    C("role_mask", sa.Integer, nullable=False),
    C("enabled", sa.Boolean, nullable=False),
)
chunks = sa.Table(
    "chunks",
    meta,
    C("revision", T, primary_key=True),
    C("ref", T, primary_key=True),
    C("doc_id", T, nullable=False),
    C("title", T, nullable=False),
    C("version", T, nullable=False),
    C("owner", T, nullable=False),
    C("audience", T, nullable=False),
    C("kind", T, nullable=False),
    C("location", T, nullable=False),
    C("body", sa.Text, nullable=False),
    C("tags", T, nullable=False),
    C(
        "embedding",
        Vector(384).with_variant(sa.LargeBinary(), "sqlite"),
        nullable=False,
    ),
)
sa.Index("chunks_scope", chunks.c.revision, chunks.c.audience, chunks.c.kind)
cache = sa.Table(
    "answers",
    meta,
    C("key", T, primary_key=True),
    C("expires", sa.Float, index=True),
    C("result", sa.JSON, nullable=False),
)
events = sa.Table(
    "events",
    meta,
    C("trace", T, primary_key=True),
    C("ts", sa.Float, index=True),
    C("status", T),
    C("reason", T),
    C("role", T),
    C("revision", T),
    C("duration_ms", sa.Float),
    C("spans", sa.JSON),
    C("score", sa.Float),
    C("hit", sa.Boolean),
    C("refs", sa.JSON),
)
tickets = sa.Table(
    "tickets",
    meta,
    C("id", T, primary_key=True),
    C("subject", T, index=True),
    C("scope", T, nullable=False),
    C("question", sa.Text),
    C("created", sa.Float, index=True),
    C("state", T),
    C("resolution", sa.Text),
    C("resolved_by", T),
    C("resolved_at", sa.Float),
)
case_documents = sa.Table(
    "case_required_documents",
    meta,
    C(
        "ticket_id",
        T,
        sa.ForeignKey("tickets.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    C("doc_id", T, sa.ForeignKey("managed_documents.id"), primary_key=True),
)
assignments = sa.Table(
    "case_assignments",
    meta,
    C(
        "ticket_id",
        T,
        sa.ForeignKey("tickets.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    C("owner", T, nullable=False),
    C("claimed_at", sa.Float, nullable=False),
)
case_activity = sa.Table(
    "case_activity",
    meta,
    C("id", T, primary_key=True),
    C(
        "ticket_id",
        T,
        sa.ForeignKey("tickets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    C("actor", T, nullable=False),
    C("action", T, nullable=False),
    C("ts", sa.Float, nullable=False),
)
ticket_queue_index = sa.Index(
    "tickets_scope_state_created", tickets.c.scope, tickets.c.state, tickets.c.created
)


def engine_for(url=None):
    STATE.mkdir(parents=True, exist_ok=True)
    url = (
        url
        or os.getenv("DATABASE_URL")
        or f"sqlite:///{(STATE / 'knowledge.db').as_posix()}"
    )
    options = {}
    if sa.engine.make_url(url).get_backend_name() == "postgresql":
        options.update(
            pool_size=int(os.getenv("DB_POOL_SIZE", "8")),
            max_overflow=int(os.getenv("DB_POOL_OVERFLOW", "8")),
        )
    engine = sa.create_engine(url, pool_pre_ping=True, **options)
    if engine.dialect.name == "sqlite":

        @sa.event.listens_for(engine, "connect")
        def configure(conn, _):
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=15000")
            conn.execute("PRAGMA journal_mode=WAL")

    return engine


def initialize(engine):
    with engine.begin() as conn:
        if engine.dialect.name == "postgresql":
            conn.execute(
                sa.text(
                    "SELECT pg_advisory_xact_lock(hashtext('folio-schema-bootstrap'))"
                )
            )
            conn.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))
        upgrade_cases = sa.inspect(conn).has_table("tickets") and not sa.inspect(
            conn
        ).has_table(case_documents.name)
        meta.create_all(conn)
        if upgrade_cases:
            # Older tickets do not record which private files were available at creation.
            # Conservatively protect that backlog with every restricted managed file.
            rows = (
                sa.select(tickets.c.id, documents.c.id)
                .select_from(tickets.join(documents, sa.true()))
                .where(documents.c.role_mask.bitwise_and(ROLE_BITS["reviewer"]) == 0)
            )
            conn.execute(
                case_documents.insert().from_select(["ticket_id", "doc_id"], rows)
            )
        conn.execute(
            insert_for(conn, pdf_epoch)
            .values(id=1, value="")
            .on_conflict_do_nothing(index_elements=["id"])
        )
        conn.execute(
            insert_for(conn, tool_controls)
            .values(name="case_status", enabled=True, version=1)
            .on_conflict_do_nothing(index_elements=["name"])
        )
        for purpose in ("chat", "embedding"):
            conn.execute(
                insert_for(conn, provider_routes)
                .values(purpose=purpose, version=1)
                .on_conflict_do_nothing(index_elements=["purpose"])
            )
        # create_all skips indexes on pre-existing tables when upgrading a live installation.
        ticket_queue_index.create(conn, checkfirst=True)
        if engine.dialect.name == "postgresql":
            conn.execute(
                sa.text(
                    "CREATE INDEX IF NOT EXISTS chunks_hnsw ON chunks USING hnsw (embedding vector_cosine_ops)"
                )
            )
        if engine.dialect.name == "postgresql" and conn.scalar(
            sa.text("SELECT 1 FROM pg_roles WHERE rolname='desk_web'")
        ):
            conn.execute(
                sa.text(
                    "GRANT SELECT ON accounts,settings,chunks,answers,tickets,events TO desk_web"
                )
            )
            conn.execute(sa.text("GRANT INSERT,UPDATE ON answers,tickets TO desk_web"))
            conn.execute(
                sa.text("GRANT SELECT,INSERT ON case_required_documents TO desk_web")
            )
            conn.execute(sa.text("GRANT INSERT ON events TO desk_web"))
            conn.execute(
                sa.text("GRANT SELECT,INSERT,UPDATE ON conversations TO desk_web")
            )
            conn.execute(
                sa.text(
                    "GRANT SELECT,INSERT ON private_chat_files,conversation_turns,tool_changes TO desk_web"
                )
            )
            conn.execute(
                sa.text(
                    "GRANT SELECT,UPDATE ON tool_controls,model_provider_routes TO desk_web"
                )
            )
            conn.execute(
                sa.text(
                    "GRANT SELECT,INSERT,UPDATE,DELETE ON model_provider_profiles TO desk_web"
                )
            )
            conn.execute(sa.text("GRANT SELECT,DELETE ON login_invites TO desk_web"))
            conn.execute(
                sa.text("GRANT SELECT,INSERT,DELETE ON web_sessions TO desk_web")
            )
            conn.execute(
                sa.text("GRANT SELECT,INSERT,DELETE ON case_assignments TO desk_web")
            )
            conn.execute(sa.text("GRANT SELECT,INSERT ON case_activity TO desk_web"))
            conn.execute(
                sa.text("GRANT SELECT,INSERT ON managed_documents TO desk_web")
            )
            conn.execute(
                sa.text(
                    "GRANT UPDATE (role_mask,enabled,version,updated,actor) ON managed_documents TO desk_web"
                )
            )
            conn.execute(
                sa.text(
                    "GRANT INSERT ON managed_pdf_files,managed_document_events TO desk_web"
                )
            )
            conn.execute(
                sa.text("GRANT SELECT,UPDATE (value) ON managed_pdf_epoch TO desk_web")
            )
            conn.execute(sa.text("GRANT SELECT,INSERT ON departments TO desk_web"))
            conn.execute(
                sa.text("GRANT SELECT,INSERT,UPDATE ON sso_people TO desk_web")
            )
            conn.execute(
                sa.text(
                    "GRANT SELECT,INSERT,DELETE ON department_members,managed_document_grants TO desk_web"
                )
            )
            conn.execute(sa.text("GRANT SELECT ON managed_pdf_files TO desk_web"))
            conn.execute(
                sa.text("ALTER TABLE managed_pdf_files ENABLE ROW LEVEL SECURITY")
            )
            bits = " ".join(
                f"WHEN '{role}' THEN {bit}" for role, bit in ROLE_BITS.items()
            )
            managed_read = f"""EXISTS (
                SELECT 1 FROM managed_documents d WHERE d.id={{outer_id}}
                AND d.status='ready' AND d.enabled
                AND ((d.role_mask & (CASE current_setting('desk.role',true) {bits} ELSE 0 END))<>0
                    OR EXISTS (
                        SELECT 1 FROM managed_document_grants g
                        WHERE g.doc_id=d.id AND (
                            (g.kind='person' AND g.target=current_setting('desk.subject',true))
                            OR (g.kind='department' AND EXISTS (
                                SELECT 1 FROM department_members m
                                WHERE m.department=g.target AND m.subject=current_setting('desk.subject',true)
                            ))
                        )
                    ))
            )"""
            conn.execute(
                sa.text("DROP POLICY IF EXISTS desk_pdf_read ON managed_pdf_files")
            )
            conn.execute(
                sa.text(
                    "CREATE POLICY desk_pdf_read ON managed_pdf_files FOR SELECT TO desk_web USING ("
                    + managed_read.format(outer_id="managed_pdf_files.doc_id")
                    + ")"
                )
            )
            conn.execute(
                sa.text("DROP POLICY IF EXISTS desk_pdf_upload ON managed_pdf_files")
            )
            conn.execute(
                sa.text(
                    "CREATE POLICY desk_pdf_upload ON managed_pdf_files FOR INSERT TO desk_web "
                    "WITH CHECK (EXISTS (SELECT 1 FROM managed_documents d "
                    "WHERE d.id=managed_pdf_files.doc_id AND d.status='queued'))"
                )
            )
            conn.execute(sa.text("ALTER TABLE chunks ENABLE ROW LEVEL SECURITY"))
            conn.execute(sa.text("ALTER TABLE chunks FORCE ROW LEVEL SECURITY"))
            conn.execute(sa.text("DROP POLICY IF EXISTS desk_visible ON chunks"))
            conn.execute(
                sa.text(f"""CREATE POLICY desk_visible ON chunks FOR SELECT TO desk_web USING (
                (revision=(SELECT value FROM settings WHERE name='revision')
                 AND current_setting('desk.role',true) IN ('employee','reviewer','executive','admin')
                 AND (audience='staff' OR (audience='executive' AND current_setting('desk.role',true)='executive')))
                OR (revision='{MANAGED_REVISION}' AND {managed_read.format(outer_id="chunks.doc_id")}))""")
            )


def set_scope(conn, role, subject=""):
    if conn.dialect.name == "postgresql":
        conn.execute(
            sa.text(
                "SELECT set_config('desk.role', :role, true), set_config('desk.subject', :subject, true)"
            ),
            {"role": role, "subject": subject},
        )


def insert_for(conn, table):
    return (pg_insert if conn.dialect.name == "postgresql" else lite_insert)(table)


def base_revision():
    return (
        sa.select(settings.c.value)
        .where(settings.c.name == "revision")
        .scalar_subquery()
    )


def revision_value():
    uploaded = sa.select(pdf_epoch.c.value).where(pdf_epoch.c.id == 1).scalar_subquery()
    return base_revision() + sa.case(
        (uploaded != "", sa.literal(":") + uploaded), else_=""
    )


def revision(conn):
    return conn.scalar(sa.select(revision_value()))


def visible_chunks(role, base, subject=""):
    seed = sa.and_(
        chunks.c.revision == base,
        chunks.c.audience.in_(
            ["staff", "executive"] if role == "executive" else ["staff"]
        ),
    )
    managed = sa.and_(
        chunks.c.revision == MANAGED_REVISION,
        sa.exists(sa.select(1).where(managed_access(chunks.c.doc_id, role, subject))),
    )
    return sa.or_(seed, managed)


def grant_target(subject):
    member_of = sa.select(department_members.c.department).where(
        department_members.c.subject == subject
    )
    return sa.or_(
        sa.and_(doc_grants.c.kind == "person", doc_grants.c.target == subject),
        sa.and_(doc_grants.c.kind == "department", doc_grants.c.target.in_(member_of)),
    )


def managed_access(doc_id, role, subject):
    scoped = sa.exists(
        sa.select(1).where(
            doc_grants.c.doc_id == documents.c.id,
            grant_target(subject),
        )
    )
    return sa.and_(
        documents.c.id == doc_id,
        documents.c.status == "ready",
        documents.c.enabled.is_(True),
        sa.or_(documents.c.role_mask.bitwise_and(ROLE_BITS.get(role, 0)) != 0, scoped),
    )


def has_scoped_access(subject):
    return sa.exists(
        sa.select(1)
        .select_from(doc_grants.join(documents, documents.c.id == doc_grants.c.doc_id))
        .where(
            documents.c.status == "ready",
            documents.c.enabled.is_(True),
            grant_target(subject),
        )
    )
