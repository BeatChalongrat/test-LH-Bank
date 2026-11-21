"""Trusted provider catalog and encrypted API profiles shared by web and indexing workers."""

import os
from urllib.parse import urlsplit

import sqlalchemy as sa
from cryptography.fernet import Fernet, InvalidToken

from .db import provider_profiles, provider_routes

CATALOG = {
    "anthropic": ("Anthropic Claude", "https://api.anthropic.com/v1", ("chat",)),
    "openai": ("OpenAI", "https://api.openai.com/v1", ("chat", "embedding")),
    "groq": ("Groq", "https://api.groq.com/openai/v1", ("chat",)),
    "openrouter": ("OpenRouter", "https://openrouter.ai/api/v1", ("chat", "embedding")),
    "gemini": (
        "Google Gemini",
        "https://generativelanguage.googleapis.com/v1beta/openai",
        ("chat", "embedding"),
    ),
}


def catalog():
    entries = CATALOG.copy()
    for entry in os.getenv("PROVIDER_GATEWAYS", "").split(","):
        endpoint = entry.strip().rstrip("/")
        if not endpoint:
            continue
        url = urlsplit(endpoint)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise ValueError("PROVIDER_GATEWAYS requires approved HTTPS base URLs")
        entries[
            "gateway-"
            + url.hostname
            + (url.path or "")
            + (":" + str(url.port) if url.port else "")
        ] = (url.netloc + url.path, endpoint, ("chat", "embedding"))
    return entries


class ProfileConfigurationError(RuntimeError):
    pass


def cipher():
    try:
        return Fernet(os.environ["PROVIDER_ENCRYPTION_KEY"].encode())
    except (KeyError, ValueError) as exc:
        raise ProfileConfigurationError(
            "Run scripts/configure.py and recreate the service"
        ) from exc


def key_for(profile):
    try:
        return cipher().decrypt(profile["secret"].encode()).decode()
    except InvalidToken as exc:
        raise ProfileConfigurationError("Restore the provider encryption key") from exc


def active_profile(conn, purpose):
    return (
        conn.execute(
            sa.select(provider_profiles)
            .select_from(
                provider_routes.join(
                    provider_profiles,
                    provider_routes.c.profile_id == provider_profiles.c.id,
                )
            )
            .where(provider_routes.c.purpose == purpose)
        )
        .mappings()
        .first()
    )


def allowed_profile(profile):
    entry = catalog().get(profile["provider"])
    return entry and entry[1] == profile["endpoint"] and profile["purpose"] in entry[2]
