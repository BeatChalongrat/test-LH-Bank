"""Browser identity. API clients keep using the existing bearer-token contract."""

import hashlib
import logging
import os
import secrets
import time
from urllib.parse import urlsplit

import anyio
import httpx
import sqlalchemy as sa
from authlib.integrations.base_client.errors import OAuthError
from authlib.integrations.starlette_client import OAuth
from fastapi import HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from joserfc.errors import JoseError
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.sessions import SessionMiddleware

from .db import accounts, insert_for, invites, sessions
from .directory import register_sso
from .security import User, oidc_user, validate_account


def digest(secret):
    return hashlib.sha256(secret.encode()).hexdigest()


def issue_invite(conn, name, role):
    if os.getenv("LOCAL_AUTH", "1") != "1":
        raise ValueError("Local account invitations are disabled")
    validate_account(name, role)
    # Two operators may invite the same new employee concurrently. Existing grants win.
    conn.execute(
        insert_for(conn, accounts)
        .values(subject=name, role=role, token_hash=digest(secrets.token_urlsafe(32)))
        .on_conflict_do_nothing(index_elements=["subject"])
    )
    row = (
        conn.execute(sa.select(accounts).where(accounts.c.subject == name))
        .mappings()
        .one()
    )
    if row["role"] != role:
        raise ValueError("Existing account has a different role")
    code = secrets.token_urlsafe(32)
    conn.execute(
        invites.insert().values(
            digest=digest(code),
            account_hash=row["token_hash"],
            expires=time.time() + 600,
        )
    )
    return code


class Invite(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    code: str = Field(min_length=20, max_length=128)


def install_browser_auth(app):
    origin = os.getenv("PORTAL_ORIGIN", "http://127.0.0.1:8765").rstrip("/")
    url = urlsplit(origin)
    https = url.scheme == "https"
    if (
        (
            not https
            and not (
                url.scheme == "http"
                and url.hostname in {"localhost", "127.0.0.1", "::1"}
            )
        )
        or not url.netloc
        or url.path
        or url.query
        or url.fragment
        or url.username
        or url.password
    ):
        raise ValueError("PORTAL_ORIGIN must be an HTTPS origin or HTTP loopback")
    cookie = "__Host-desk_session" if https else "desk_session"
    local = os.getenv("LOCAL_AUTH", "1") == "1"
    issuer = os.getenv("OIDC_ISSUER", "")
    client_id = os.getenv("OIDC_CLIENT_ID", "")
    provider = None
    if client_id:
        signing = os.getenv("BROWSER_SIGNING_KEY", "")
        groups = [
            group
            for role in ("EMPLOYEE", "REVIEWER", "EXECUTIVE", "ADMIN")
            if (group := os.getenv("OIDC_GROUP_" + role))
        ]
        if (
            "openid" not in os.getenv("OIDC_SCOPES", "openid profile").split()
            or not groups
            or len(groups) != len(set(groups))
        ):
            raise ValueError(
                "Browser SSO requires the openid scope and distinct configured role groups"
            )
        if (
            not https
            or not issuer.startswith("https://")
            or len(signing) < 32
            or not os.getenv("OIDC_CLIENT_SECRET")
        ):
            raise ValueError(
                "Browser SSO requires HTTPS portal and issuer, client secret and signing key"
            )
        app.add_middleware(
            SessionMiddleware,
            secret_key=signing,
            session_cookie="__Host-desk_flow",
            max_age=600,
            https_only=True,
            same_site="lax",
        )
        provider = OAuth().register(
            name="organization",
            server_metadata_url=issuer.rstrip("/")
            + "/.well-known/openid-configuration",
            client_id=client_id,
            client_secret=os.environ["OIDC_CLIENT_SECRET"],
            client_kwargs={
                "scope": os.getenv("OIDC_SCOPES", "openid profile"),
                "code_challenge_method": "S256",
            },
        )

    def same_origin(request):
        if (
            request.headers.get("x-desk-request") != "1"
            or request.headers.get("origin", origin) != origin
        ):
            raise HTTPException(403, "คำขอนี้ต้องส่งจากพอร์ทัล")

    def session_user(request):
        secret = request.cookies.get(cookie)
        if not secret or len(secret) > 128:
            return None
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            same_origin(request)
        with app.state.service.engine.connect() as conn:
            row = (
                conn.execute(
                    sa.select(sessions, accounts.c.role.label("current_role"))
                    .select_from(
                        sessions.outerjoin(
                            accounts, sessions.c.account_hash == accounts.c.token_hash
                        )
                    )
                    .where(
                        sessions.c.digest == digest(secret),
                        sessions.c.expires > time.time(),
                    )
                )
                .mappings()
                .first()
            )
        if not row:
            return None
        if row["account_hash"]:
            return (
                User(row["subject"], row["current_role"])
                if local and row["current_role"]
                else None
            )
        return User(row["subject"], row["role"]) if provider else None

    def create_session(conn, request, response, user, lifetime, account_hash=None):
        old = request.cookies.get(cookie)
        if old:
            conn.execute(sessions.delete().where(sessions.c.digest == digest(old)))
        secret = secrets.token_urlsafe(32)
        conn.execute(
            sessions.insert().values(
                digest=digest(secret),
                subject=user.subject,
                role=user.role,
                account_hash=account_hash,
                expires=time.time() + lifetime,
            )
        )
        response.set_cookie(
            cookie,
            secret,
            httponly=True,
            secure=https,
            samesite="lax",
            max_age=lifetime,
            path="/",
        )

    @app.get("/api/auth/options")
    def options():
        return {"organization": bool(provider), "invitation": local}

    @app.post("/api/auth/invite")
    def exchange(body: Invite, request: Request, response: Response):
        same_origin(request)
        if not local:
            raise HTTPException(404)
        with app.state.service.engine.begin() as conn:
            account_hash = conn.scalar(
                invites.delete()
                .where(
                    invites.c.digest == digest(body.code),
                    invites.c.expires > time.time(),
                )
                .returning(invites.c.account_hash)
            )
            row = (
                conn.execute(
                    sa.select(accounts.c.subject, accounts.c.role).where(
                        accounts.c.token_hash == account_hash
                    )
                ).first()
                if account_hash
                else None
            )
            if not row:
                raise HTTPException(401, "รหัสเข้าใช้งานหมดอายุหรือถูกใช้แล้ว")
            user = User("local/" + row.subject, row.role)
            create_session(conn, request, response, user, 8 * 3600, account_hash)
        return {"subject": user.subject, "role": user.role}

    @app.post("/api/auth/logout")
    def logout(request: Request, response: Response):
        same_origin(request)
        with app.state.service.engine.begin() as conn:
            conn.execute(
                sessions.delete().where(
                    sessions.c.digest == digest(request.cookies.get(cookie, ""))
                )
            )
        response.delete_cookie(
            cookie, path="/", httponly=True, secure=https, samesite="lax"
        )
        return {"signed_out": True}

    @app.get("/auth/login")
    async def login(request: Request):
        if not provider:
            raise HTTPException(404)
        try:
            return await provider.authorize_redirect(request, origin + "/auth/callback")
        except (OAuthError, httpx.HTTPError):
            return RedirectResponse("/?login=unavailable", 303)

    @app.get("/auth/callback")
    async def callback(request: Request):
        if not provider:
            raise HTTPException(404)
        try:
            token = await provider.authorize_access_token(
                request,
                claims_options={"iss": {"essential": True, "value": issuer}},
                leeway=15,
            )
            claims = token.get("userinfo") or {}
            user = oidc_user(claims)
            lifetime = min(3600, int(claims.get("exp", 0) - time.time()))
            if not user or lifetime <= 0 or claims.get("iss") != issuer:
                return RedirectResponse("/?login=denied", 303)
        except (
            OAuthError,
            JoseError,
            httpx.HTTPError,
            ValueError,
            TypeError,
            KeyError,
        ):
            logging.getLogger("uvicorn.error").warning("Browser SSO callback rejected")
            return RedirectResponse("/?login=denied", 303)
        response = RedirectResponse("/", 303)

        def persist():
            with app.state.service.engine.begin() as conn:
                label = claims.get("name") or claims.get("preferred_username")
                register_sso(
                    conn,
                    user.subject,
                    label[:160] if isinstance(label, str) else user.subject,
                )
                create_session(conn, request, response, user, lifetime)

        await anyio.to_thread.run_sync(persist)
        return response

    return session_user
