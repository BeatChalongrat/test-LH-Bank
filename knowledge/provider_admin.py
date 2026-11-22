import os
import time
from typing import Literal
from uuid import uuid4

import httpx
import sqlalchemy as sa
from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from .db import provider_profiles as profiles, provider_routes as routes, settings
from .embedding_api import ApiEncoder, EmbeddingUnavailable, embedding_space
from .provider import ProviderCoolingDown
from .provider_profiles import catalog, cipher, ProfileConfigurationError
from .service import Generator


class Profile(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=80)
    provider: str = Field(min_length=1, max_length=300)
    purpose: Literal["chat", "embedding"]
    model: str = Field(min_length=1, max_length=150, pattern=r"^[A-Za-z0-9_./:@+-]+$")
    api_key: SecretStr


class Change(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)


class Rotate(Change):
    api_key: SecretStr


class Activation(Change):
    profile_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")


def seal(value):
    key = value.get_secret_value().strip()
    if (
        not 1 <= len(key) <= 8192
        or not key.isascii()
        or any(ord(c) < 33 or ord(c) > 126 for c in key)
    ):
        raise HTTPException(400, "กรุณาระบุ API key ที่ใช้งานได้")
    try:
        return cipher().encrypt(key.encode()).decode()
    except ProfileConfigurationError:
        raise HTTPException(503, "กรุณาตั้งค่าคีย์เข้ารหัสของระบบและเปิดบริการใหม่") from None


def install_providers(app, current):
    def admin(user=Depends(current)):
        if user.role != "admin":
            raise HTTPException(403)
        return user

    @app.get("/api/admin/providers")
    def inventory(_=Depends(admin)):
        engine = app.state.service.engine
        with engine.connect() as conn:
            entries = (
                conn.execute(
                    sa.select(*(c for c in profiles.c if c.name != "secret")).order_by(
                        profiles.c.name
                    )
                )
                .mappings()
                .all()
            )
            active = conn.execute(sa.select(routes)).mappings().all()
            indexed = conn.scalar(
                sa.select(settings.c.value).where(settings.c.name == "revision")
            )
        return {
            "providers": [
                {"id": k, "name": v[0], "purposes": v[2]} for k, v in catalog().items()
            ],
            "profiles": entries,
            "routes": active,
            "indexed": bool(indexed),
            "chat_configured": getattr(app.state.service.generator, "url", "")
            not in {"", "https://unconfigured.invalid/v1"},
            "embedding_mode": os.getenv("EMBEDDING_MODE", "local"),
        }

    @app.post("/api/admin/providers", status_code=201)
    def save(body: Profile, user=Depends(admin)):
        entry = catalog().get(body.provider)
        if not entry or body.purpose not in entry[2]:
            raise HTTPException(400, "ผู้ให้บริการนี้ไม่รองรับงานที่เลือก")
        identity = uuid4().hex
        with app.state.service.engine.begin() as conn:
            conn.execute(
                profiles.insert().values(
                    id=identity,
                    name=body.name,
                    provider=body.provider,
                    endpoint=entry[1],
                    model=body.model,
                    purpose=body.purpose,
                    secret=seal(body.api_key),
                    version=1,
                    actor=user.subject,
                    updated=time.time(),
                )
            )
        return {"id": identity}

    @app.post("/api/admin/providers/{identity}/key")
    def rotate(identity: str, body: Rotate, user=Depends(admin)):
        with app.state.service.engine.begin() as conn:
            updated = conn.execute(
                profiles.update()
                .where(profiles.c.id == identity, profiles.c.version == body.version)
                .values(
                    secret=seal(body.api_key),
                    version=body.version + 1,
                    actor=user.subject,
                    updated=time.time(),
                )
            )
            if not updated.rowcount:
                raise HTTPException(409, "ข้อมูลเปลี่ยนแล้ว กรุณาโหลดใหม่")
        return {"version": body.version + 1}

    @app.post("/api/admin/providers/{identity}/remove")
    def remove(identity: str, body: Change, _=Depends(admin)):
        try:
            with app.state.service.engine.begin() as conn:
                deleted = conn.execute(
                    profiles.delete().where(
                        profiles.c.id == identity,
                        profiles.c.version == body.version,
                        ~sa.exists(
                            sa.select(1).where(routes.c.profile_id == profiles.c.id)
                        ),
                    )
                )
                if not deleted.rowcount:
                    raise HTTPException(409, "โปรไฟล์นี้กำลังใช้งานหรือถูกแก้ไขแล้ว")
        except sa.exc.IntegrityError:
            raise HTTPException(409, "โปรไฟล์นี้กำลังใช้งาน") from None
        return {"removed": True}

    @app.post("/api/admin/providers/{identity}/test")
    def test_connection(identity: str, _=Depends(admin)):
        with app.state.service.engine.connect() as conn:
            profile = (
                conn.execute(sa.select(profiles).where(profiles.c.id == identity))
                .mappings()
                .first()
            )
        if not profile:
            raise HTTPException(404)
        start = time.perf_counter()
        client = None
        try:
            if profile["purpose"] == "chat":
                model = Generator(profile)
                client = model.client
                result = model.select(
                    "What color is the sample card?",
                    [
                        {
                            "ref": "SAMPLE#1",
                            "title": "Sample",
                            "body": "The sample card is blue.",
                        }
                    ],
                )
                if result.index != 1:
                    raise ValueError("Unconfirmed selector")
            else:
                model = ApiEncoder(profile)
                client = model.client
                model.encode(["sample card"], "query")
        except (
            httpx.HTTPError,
            ProviderCoolingDown,
            EmbeddingUnavailable,
            ProfileConfigurationError,
            ValueError,
            KeyError,
            IndexError,
            TypeError,
        ):
            raise HTTPException(
                422, "ทดสอบไม่ผ่าน ตรวจคีย์ ชื่อรุ่น โควตา และรูปแบบผลลัพธ์ที่โมเดลรองรับ"
            ) from None
        finally:
            if client is not None:
                client.close()
        return {"ok": True, "latency_ms": round((time.perf_counter() - start) * 1000)}

    @app.post("/api/admin/provider-routes/{purpose}")
    def activate(
        purpose: Literal["chat", "embedding"], body: Activation, user=Depends(admin)
    ):
        with app.state.service.engine.begin() as conn:
            if body.profile_id:
                row = (
                    conn.execute(
                        sa.select(profiles).where(
                            profiles.c.id == body.profile_id,
                            profiles.c.purpose == purpose,
                        )
                    )
                    .mappings()
                    .first()
                )
                if not row:
                    raise HTTPException(404)
            if purpose == "embedding":
                if os.getenv("EMBEDDING_MODE") != "managed" or not body.profile_id:
                    raise HTTPException(
                        409, "เลือกโปรไฟล์ embeddings ในโหมดตั้งค่าผ่าน Admin"
                    )
                previous = conn.scalar(
                    sa.select(routes.c.profile_id).where(routes.c.purpose == purpose)
                )
                space = embedding_space(row["endpoint"], row["model"], row["model"])
                stored = conn.scalar(
                    sa.select(settings.c.value).where(
                        settings.c.name == "embedding_space"
                    )
                )
                if previous and previous != body.profile_id:
                    old = (
                        conn.execute(
                            sa.select(profiles).where(profiles.c.id == previous)
                        )
                        .mappings()
                        .one()
                    )
                    if (old["endpoint"], old["model"]) != (
                        row["endpoint"],
                        row["model"],
                    ):
                        raise HTTPException(
                            409,
                            "การเปลี่ยน embedding model ต้องจัดทำดัชนีใหม่ในช่วง maintenance",
                        )
                indexed = conn.scalar(
                    sa.select(settings.c.value).where(settings.c.name == "revision")
                )
                if (stored and stored != space) or (indexed and not stored):
                    raise HTTPException(409, "กรุณาย้ายดัชนีเดิมด้วยคำสั่ง reembed ก่อนเปิดโหมดนี้")
            changed = conn.execute(
                routes.update()
                .where(routes.c.purpose == purpose, routes.c.version == body.version)
                .values(
                    profile_id=body.profile_id,
                    actor=user.subject,
                    updated=time.time(),
                    version=body.version + 1,
                )
            )
            if not changed.rowcount:
                raise HTTPException(409, "มีผู้เปลี่ยนการตั้งค่าก่อนหน้า กรุณาโหลดใหม่")
        return {"version": body.version + 1}
