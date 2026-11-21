import math
import os
import secrets
import time
from contextlib import asynccontextmanager
from functools import partial

import anyio
import sqlalchemy as sa
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from .ingest import check_space
from .agent import install_agent
from .db import ROOT, engine_for, events, revision, tickets
from .documents import install_documents
from .embedding_api import configured_encoder
from .security import authenticate
from .service import Evidence, Generator, Service
from .web_auth import install_browser_auth
from .workspace import install_workspace


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    question: str = Field(min_length=2, max_length=1200)


async def run_stage(gate, workers, wait, backlog, fn, *args):
    """Acquire before entering a worker thread. Return None on bounded queue overflow."""
    started = time.perf_counter()
    if gate.value == 0 and gate.statistics().tasks_waiting >= backlog:
        return None, 0.0
    with anyio.move_on_after(wait) as scope:
        await gate.acquire()
    delay = (time.perf_counter() - started) * 1000
    if scope.cancelled_caught:
        return None, delay
    try:
        return await anyio.to_thread.run_sync(fn, *args, limiter=workers), delay
    finally:
        gate.release()


def create_app(service=None):
    @asynccontextmanager
    async def lifespan(app):
        managed = service is None
        app.state.service = service or Service(
            engine_for(), configured_encoder(), Generator()
        )
        if managed:
            with app.state.service.engine.connect() as conn:
                check_space(conn, app.state.service.encoder)
        in_flight = int(os.getenv("INFERENCE_IN_FLIGHT", "16"))
        if not 1 <= in_flight <= 256:
            raise ValueError("INFERENCE_IN_FLIGHT must be 1–256")
        app.state.inference_backlog = int(
            os.getenv("INFERENCE_MAX_WAITERS", str(in_flight * 2))
        )
        if not 0 <= app.state.inference_backlog <= 4096:
            raise ValueError("INFERENCE_MAX_WAITERS must be 0–4096")
        app.state.inference_gate = anyio.Semaphore(in_flight)
        app.state.lookup_workers = anyio.CapacityLimiter(
            int(os.getenv("RETRIEVAL_IN_FLIGHT", "16"))
        )
        app.state.lookup_gate = anyio.Semaphore(app.state.lookup_workers.total_tokens)
        app.state.model_workers = anyio.CapacityLimiter(in_flight)
        app.state.inference_wait = float(os.getenv("INFERENCE_QUEUE_TIMEOUT", "8"))
        if not math.isfinite(app.state.inference_wait) or app.state.inference_wait <= 0:
            raise ValueError("INFERENCE_QUEUE_TIMEOUT must be finite and positive")
        try:
            yield
        finally:
            if managed:
                app.state.service.generator.client.close()
                if client := getattr(app.state.service.encoder, "client", None):
                    client.close()
                app.state.service.engine.dispose()

    app = FastAPI(
        title="Enterprise Knowledge & Support", version="1.0.0", lifespan=lifespan
    )
    bearer = HTTPBearer(auto_error=False)

    session_user = install_browser_auth(app)

    def current(
        request: Request, cred: HTTPAuthorizationCredentials | None = Depends(bearer)
    ):
        if cred is None:
            user = session_user(request)
        elif len(cred.credentials) <= 16384:
            user = authenticate(app.state.service.engine, cred.credentials)
        else:
            user = None
        if not user:
            raise HTTPException(
                401, "กรุณาเข้าสู่ระบบอีกครั้ง", headers={"WWW-Authenticate": "Bearer"}
            )
        return user

    @app.middleware("http")
    async def headers(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=()"
        )
        if request.url.path in {"/", "/admin"}:
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; object-src 'none'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
            )
        return response

    @app.get("/", include_in_schema=False)
    @app.get("/admin", include_in_schema=False)
    def index():
        return FileResponse(ROOT / "static/index.html")

    @app.get("/health/live")
    def live():
        return {"status": "ok"}

    @app.get("/health/ready")
    def ready():
        try:
            with app.state.service.engine.connect() as conn:
                rev = revision(conn)
        except sa.exc.SQLAlchemyError:
            rev = None
        if not rev:
            raise HTTPException(503, "Index unavailable")
        return {"status": "ready", "revision": rev}

    @app.get("/api/me")
    def me(user=Depends(current)):
        return {"subject": user.subject, "role": user.role}

    async def answer(user, question, on_commit=None, original_question=None):
        service = app.state.service
        finish = (
            partial(
                service.finish, on_commit=on_commit, original_question=original_question
            )
            if on_commit
            else service.finish
        )
        request = await anyio.to_thread.run_sync(service.prepare, user, question)
        if not request.needs_inference:
            return await anyio.to_thread.run_sync(finish, user, question, request)
        evidence, lookup_wait = await run_stage(
            app.state.lookup_gate,
            app.state.lookup_workers,
            app.state.inference_wait,
            app.state.inference_backlog,
            service.lookup,
            user,
            request,
        )
        if evidence is None:
            evidence = Evidence(0.0, [], "retrieval_overloaded", {})
        evidence.timings["lookup_admission_ms"] = round(lookup_wait, 2)
        selected, queue_ms, overloaded = None, 0.0, False
        if evidence.choices:
            selected, queue_ms = await run_stage(
                app.state.inference_gate,
                app.state.model_workers,
                app.state.inference_wait,
                app.state.inference_backlog,
                service.generate,
                question,
                evidence.choices,
            )
            overloaded = selected is None
        return await anyio.to_thread.run_sync(
            finish, user, question, request, evidence, selected, queue_ms, overloaded
        )

    @app.post("/api/ask")
    async def ask(body: Question, user=Depends(current)):
        return await answer(user, body.question)

    install_agent(app, current, answer)
    install_documents(app, current)
    install_workspace(app, current)

    @app.get("/api/metrics", response_class=PlainTextResponse)
    def metrics(
        request: Request, cred: HTTPAuthorizationCredentials | None = Depends(bearer)
    ):
        secret = os.getenv("METRICS_TOKEN", "")
        if not (
            cred
            and secret
            and secrets.compare_digest(cred.credentials.encode(), secret.encode())
        ):
            if current(request, cred).role != "reviewer":
                raise HTTPException(403)
        with app.state.service.engine.connect() as conn:
            rows = conn.execute(
                sa.select(events.c.status, events.c.reason, sa.func.count())
                .where(events.c.ts >= time.time() - 86400)
                .group_by(events.c.status, events.c.reason)
            ).all()

            def percentile(window):
                where = events.c.ts >= time.time() - window
                if conn.dialect.name == "postgresql":
                    return (
                        conn.scalar(
                            sa.select(
                                sa.func.percentile_cont(0.95).within_group(
                                    events.c.duration_ms
                                )
                            ).where(where)
                        )
                        or 0
                    )
                times = list(
                    conn.scalars(
                        sa.select(events.c.duration_ms)
                        .where(where)
                        .order_by(events.c.duration_ms)
                    )
                )
                return times[math.ceil(0.95 * len(times)) - 1] if times else 0

            day_p95, recent_p95 = percentile(86400), percentile(600)
            recent = conn.execute(
                sa.select(events.c.reason, sa.func.count())
                .where(events.c.ts >= time.time() - 300)
                .group_by(events.c.reason)
            ).all()
            oldest = conn.scalar(
                sa.select(sa.func.min(tickets.c.created)).where(
                    tickets.c.state.in_(["open", "in_progress"])
                )
            )
            hits = conn.scalar(
                sa.select(sa.func.count())
                .select_from(events)
                .where(events.c.hit.is_(True), events.c.ts >= time.time() - 86400)
            )
        lines = ["# TYPE knowledge_queries_24h gauge"] + [
            f'knowledge_queries_24h{{outcome="{s}",reason="{r}"}} {n}'
            for s, r, n in rows
        ]
        lines += [f'knowledge_queries_5m{{reason="{r}"}} {n}' for r, n in recent]
        lines += [
            f"knowledge_retrieval_waiters {app.state.lookup_gate.statistics().tasks_waiting}",
            f"knowledge_inference_slots_available {app.state.inference_gate.value}",
            f"knowledge_inference_waiters {app.state.inference_gate.statistics().tasks_waiting}",
            f"knowledge_cache_hits_24h {hits}",
            f"knowledge_latency_p95_ms_24h {day_p95}",
            f"knowledge_latency_p95_ms_10m {recent_p95}",
            f"knowledge_oldest_open_seconds {int(time.time() - oldest) if oldest else 0}",
        ]
        return "\n".join(lines) + "\n"

    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
    return app


app = create_app()
