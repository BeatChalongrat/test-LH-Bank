import hashlib
import os

import httpx
import numpy as np

from .provider import ProviderBackoff


class EmbeddingUnavailable(RuntimeError):
    pass


def configured_encoder():
    mode = os.getenv("EMBEDDING_MODE", "local")
    if mode == "local":
        from .encoder import Encoder

        return Encoder()
    if mode == "api":
        return ApiEncoder()
    raise ValueError("EMBEDDING_MODE must be local or api")


class ApiEncoder:
    def __init__(self):
        self.url = os.environ["EMBEDDING_BASE_URL"].rstrip("/")
        self.model = os.environ["EMBEDDING_MODEL"]
        key = os.getenv("EMBEDDING_API_KEY")
        url = httpx.URL(self.url)
        if (
            not url.host
            or url.scheme not in {"http", "https"}
            or url.query
            or url.userinfo
            or url.fragment
            or (key and url.scheme != "https")
        ):
            raise ValueError(
                "Invalid embedding API endpoint; bearer credentials require HTTPS"
            )
        ident = "\n".join(
            (self.url, self.model, os.getenv("EMBEDDING_REVISION") or self.model, "384")
        )
        self.space = "api-" + hashlib.sha256(ident.encode()).hexdigest()
        self.backoff = ProviderBackoff()
        self.client = httpx.Client(
            headers={"Authorization": "Bearer " + key} if key else {},
            timeout=httpx.Timeout(
                float(os.getenv("EMBEDDING_TIMEOUT", "5")), connect=2
            ),
        )

    def encode(self, texts, prefix):
        self.backoff.check()
        try:
            response = self.client.post(
                self.url + "/embeddings",
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
