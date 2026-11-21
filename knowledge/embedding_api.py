import hashlib
import os
import threading
import time

import httpx
import numpy as np

from .provider_profiles import (
    active_profile,
    allowed_profile,
    key_for,
    ProfileConfigurationError,
)
from .provider import ProviderBackoff


class EmbeddingUnavailable(RuntimeError):
    pass


def configured_encoder(engine=None):
    mode = os.getenv("EMBEDDING_MODE", "local")
    if mode == "local":
        from .encoder import Encoder

        return Encoder()
    if mode == "api":
        return ApiEncoder()
    if mode == "managed":
        if engine is None:
            from .db import engine_for

            engine = engine_for()
        return ManagedEncoder(engine)
    raise ValueError("EMBEDDING_MODE must be local, api or managed")


class ManagedEncoder:
    """Resolve the embedding credential independently of chat routing."""

    def __init__(self, engine):
        self.engine = engine
        self.lock = threading.Lock()
        self.next_check = 0
        self.encoder = self.stamp = None
        self.client = httpx.Client(
            timeout=httpx.Timeout(float(os.getenv("EMBEDDING_TIMEOUT", "4")), connect=2)
        )

    def current(self):
        with self.lock:
            if time.monotonic() >= self.next_check:
                with self.engine.connect() as conn:
                    row = active_profile(conn, "embedding")
                stamp = (row["id"], row["version"]) if row else None
                if stamp != self.stamp:
                    self.encoder = ApiEncoder(row, self.client) if row else None
                    self.stamp = stamp
                self.next_check = time.monotonic() + 1
            return self.encoder

    @property
    def space(self):
        current = self.current()
        return current.space if current else "setup-required"

    def encode(self, texts, prefix):
        current = self.current()
        if current is None:
            raise EmbeddingUnavailable("Select an embedding provider in Admin")
        return current.encode(texts, prefix)


def embedding_space(url, model, revision):
    ident = "\n".join((url, model, revision or model, "384"))
    return "api-" + hashlib.sha256(ident.encode()).hexdigest()


class ApiEncoder:
    def __init__(self, profile=None, client=None):
        if profile is not None:
            if not allowed_profile(profile) or profile["purpose"] != "embedding":
                raise ProfileConfigurationError("Embedding endpoint is unavailable")
            self.url, self.model, self.key = (
                profile["endpoint"],
                profile["model"],
                key_for(profile),
            )
            revision = self.model
        else:
            self.url = os.environ["EMBEDDING_BASE_URL"].rstrip("/")
            self.model = os.environ["EMBEDDING_MODEL"]
            self.key = os.getenv("EMBEDDING_API_KEY")
            revision = os.getenv("EMBEDDING_REVISION")
        url = httpx.URL(self.url)
        if (
            not url.host
            or url.scheme not in {"http", "https"}
            or url.query
            or url.userinfo
            or url.fragment
            or (self.key and url.scheme != "https")
        ):
            raise ValueError(
                "Invalid embedding API endpoint; bearer credentials require HTTPS"
            )
        self.space = embedding_space(self.url, self.model, revision)
        self.backoff = ProviderBackoff()
        self.client = client or httpx.Client(
            timeout=httpx.Timeout(
                float(os.getenv("EMBEDDING_TIMEOUT", "5")), connect=2
            ),
        )

    def encode(self, texts, prefix):
        self.backoff.check()
        try:
            response = self.client.post(
                self.url + "/embeddings",
                headers={"Authorization": "Bearer " + self.key} if self.key else {},
                json={
                    "input": texts,
                    "model": self.model,
                    "dimensions": 384,
                    "encoding_format": "float",
                },
            )
            self.backoff.observe(response)
            if response.status_code == 429:
                self.backoff.check()
            response.raise_for_status()
            rows = sorted(response.json()["data"], key=lambda row: row["index"])
            if [row["index"] for row in rows] != list(range(len(texts))):
                raise ValueError("Invalid embedding indices")
            vectors = np.asarray([row["embedding"] for row in rows], dtype=np.float32)
            if vectors.shape != (len(texts), 384) or not np.isfinite(vectors).all():
                raise ValueError("Invalid embedding vectors")
            norms = np.linalg.norm(vectors, axis=1, keepdims=True)
            if not np.isfinite(norms).all() or (norms == 0).any():
                raise ValueError("Invalid embedding norm")
            return vectors / norms
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
            raise EmbeddingUnavailable(
                "Embedding API unavailable or invalid response"
            ) from exc
