import hashlib
import os
import re
import secrets
import unicodedata
from dataclasses import dataclass
from functools import lru_cache

import jwt
import sqlalchemy as sa

from .db import accounts

ROLES = {"employee", "reviewer", "executive", "admin"}


@dataclass(frozen=True)
class User:
    subject: str
    role: str


def validate_account(name, role):
    if role not in ROLES or not re.fullmatch(r"[a-zA-Z0-9._-]{1,64}", name):
        raise ValueError("Invalid account name or role")


def provision(conn, name, role):
    validate_account(name, role)
    token = "ks_" + secrets.token_urlsafe(32)
    conn.execute(
        accounts.insert().values(
            subject=name,
            role=role,
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
        )
    )
    return token


@lru_cache(maxsize=4)
def keyset(url):
    return jwt.PyJWKClient(url, timeout=5)


def oidc_user(claims):
    groups, subject = claims.get("groups"), claims.get("sub")
    if not isinstance(groups, list) or not isinstance(subject, str) or not subject:
        return None
    for role in ("admin", "executive", "reviewer", "employee"):
        group = os.getenv("OIDC_GROUP_" + role.upper())
        if group and group in groups:
            return User("oidc/" + subject, role)
    return None


def authenticate(engine, token):
    if token.startswith("ks_") and os.getenv("LOCAL_AUTH", "1") == "1":
        with engine.connect() as conn:
            row = conn.execute(
                sa.select(accounts.c.subject, accounts.c.role).where(
                    accounts.c.token_hash == hashlib.sha256(token.encode()).hexdigest()
                )
            ).first()
        return User("local/" + row.subject, row.role) if row else None
    issuer, url, audience = (
        os.getenv(k) for k in ("OIDC_ISSUER", "OIDC_JWKS_URL", "OIDC_AUDIENCE")
    )
    if not all([issuer, url, audience]) or not url.startswith("https://"):
        return None
    try:
        key = keyset(url).get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            key.key,
            algorithms=["RS256"],
            issuer=issuer,
            audience=audience,
            options={"require": ["exp", "iat", "sub", "iss", "aud"]},
        )
        return oidc_user(claims)
    except (jwt.PyJWTError, ValueError, TypeError):
        pass
    return None


def normalize(text):
    text = unicodedata.normalize("NFC", text).lower()
    return " ".join("".join(c for c in text if unicodedata.category(c) != "Cf").split())


INJECTION = re.compile(
    r"(ignore|override|disregard|forget).{0,70}(instruction|rules|prompt)|"
    r"(system|developer)\s*prompt|admin\s*access|ยกเลิก.{0,12}คำสั่ง|ไม่ต้องทำตาม.{0,15}คำสั่ง|"
    r"(ละเลย|ลืม|เพิกเฉย).{0,20}(คำสั่ง|กฎ)|ให้สิทธิ์.{0,12}แอดมิน",
    re.I,
)
OFF_TOPIC = re.compile(
    r"พยากรณ์อากาศ|อากาศวันนี้|ผลบอล|ฟุตบอล|หวย|สูตรอาหาร|ทำอาหาร|ดวงวันนี้|weather|lottery|write a poem"
)
ORG = re.compile(
    r"บริษัท|องค์กร|พนักงาน|สวัสดิการ|นโยบาย|เบิก|ค่าเดินทาง|บิล|ใบเสร็จ|วันลา|ลาหยุด|ลา(?:พัก|ป่วย|คลอด|กิจ|ออก)|"
    r"เงินเดือน|โบนัส|ค่าแรง|ล่วงเวลา|แท็กซี่|แท๊กซี่|หัวหน้า|เคส|แชต|ฝ่าย|portal|leave|expense|salary|receipt|cs-\d"
)

# Scope-changing qualifiers in HR and expense policy. A related document alone is insufficient.
QUALIFIERS = [
    (r"ต่างประเทศ|overseas|international", r"ต่างประเทศ|overseas|international"),
    (
        r"โควตา|วันต่อปี|กี่วันในหนึ่งปี|annual allowance",
        r"โควตา|วันต่อปี|วันทำงานต่อปี|annual allowance",
    ),
    (r"ทดลองงาน|probation", r"ทดลองงาน|probation"),
    (r"พาร์ทไทม์|part.time", r"พาร์ทไทม์|part.time"),
    (r"เงินเดือน.{0,28}(เท่าไร|กี่บาท|เท่าไหร่)", r"บาท|THB"),
]


NAMED_CLAIM = re.compile(r"โครงการ\s*([ก-๙A-Za-z0-9-]{2,60}?)\s*(?:ให้เบิก|เบิก)")


def covers_qualifiers(question, text):
    # In a named scheme's claim, another scheme's similar allowance is not evidence.
    name = NAMED_CLAIM.search(question)
    if name and name[1] not in {
        "นี้",
        "นั้น",
        "ดังกล่าว",
        "เดียวกัน",
        "ของเรา",
        "ของบริษัท",
        "ไหน",
        "ใด",
        "อะไร",
        "ไหนบ้าง",
    }:
        if normalize(name[1]) not in normalize(text):
            return False
    # A procedure that requires evidence cannot confirm an exception when the employee lacks it.
    for term in ("หลักฐานการชำระเงิน", "ใบรับรองแพทย์", "ใบเสร็จ", "การอนุมัติ"):
        if re.search(r"(?:ไม่มี|ขาด|ไม่แนบ|ไม่ได้แนบ)\s*" + term, question):
            if not re.search(
                r"(?:ไม่มี|ขาด|ไม่ต้องแนบ|สูญหาย)\s*"
                + term
                + "|"
                + term
                + r"\s*(?:หาย|สูญหาย)",
                text,
            ):
                return False
    return all(
        not re.search(ask, question, re.I) or re.search(evidence, text, re.I)
        for ask, evidence in QUALIFIERS
    )


def redact(text):
    text = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[อีเมล]", text)
    text = re.sub(r"(?<!\d)(?:\d[ -]?){9,16}(?!\d)", "[เลขส่วนบุคคล]", text)
    return re.sub(r"(?i)(?:bearer\s+|ks_|sk-)[A-Za-z0-9._-]{8,}", "[คีย์]", text)
