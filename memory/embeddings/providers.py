"""
Concrete embedding providers.

Providers are stateless from the caller's perspective. The `signature`
attribute is the identity used by the migrator to detect provider changes.
"""

from __future__ import annotations

import hashlib
import logging
import math
import os
import re
from abc import ABC, abstractmethod
from typing import List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------
class BaseEmbeddingProvider(ABC):
    """Interface every provider must satisfy."""

    @property
    @abstractmethod
    def dim(self) -> int:
        """Length of vectors this provider produces."""

    @property
    @abstractmethod
    def signature(self) -> Tuple[str, str, int]:
        """
        Stable identity for migration comparison.
        Return (provider_name, model_name, dim).
        """

    @abstractmethod
    def embed(self, text: str) -> List[float]:
        """Produce a single vector."""

    def embed_batch(self, texts: Sequence[str]) -> List[List[float]]:
        """Default: sequential. Override for API providers."""
        return [self.embed(t) for t in texts]

    def is_available(self) -> bool:
        """Whether this provider can currently produce embeddings."""
        return True


# ---------------------------------------------------------------------------
# Hash — zero-dependency fallback
# ---------------------------------------------------------------------------
class HashEmbeddingProvider(BaseEmbeddingProvider):
    """
    Deterministic bag-of-words hashing.

    NOT semantically deep. Correct for distinguishing lessons that share
    surface vocabulary; useless for synonymy or paraphrase. This is the
    emergency fallback — if it is your active provider, retrieval quality
    will be mediocre.
    """

    def __init__(self, dim: int = 256) -> None:
        if dim <= 0:
            raise ValueError(f"Embedding dim must be > 0, got {dim}")
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def signature(self) -> Tuple[str, str, int]:
        return ("hash", f"bow-sha256-{self._dim}", self._dim)

    def is_available(self) -> bool:
        return True

    def embed(self, text: str) -> List[float]:
        vec = [0.0] * self._dim
        tokens = re.findall(r"[a-z0-9_]+", (text or "").lower())
        if not tokens:
            return vec
        for tok in tokens:
            digest = hashlib.sha256(tok.encode("utf-8")).digest()
            bucket = int.from_bytes(digest[:4], "big") % self._dim
            vec[bucket] += 1.0
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0.0:
            vec = [v / norm for v in vec]
        return vec


# ---------------------------------------------------------------------------
# OpenRouter — primary provider (uses existing OPENROUTER_API_KEY)
# ---------------------------------------------------------------------------
class OpenRouterEmbeddingProvider(BaseEmbeddingProvider):
    """
    Uses OpenRouter's /v1/embeddings endpoint with the existing OpenRouter
    API key. This is the same key used for chat completions, so no new
    secret is required.

    Endpoint: https://openrouter.ai/api/v1/embeddings
    Model ID: provider-prefixed, e.g. "openai/text-embedding-3-small"

    Batching: OpenRouter's embeddings endpoint accepts multiple inputs per
    request. 128 is a safe practical default.
    """

    BATCH_SIZE = 128

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "openai/text-embedding-3-small",
        dim: int = 512,
        base_url: str = "https://openrouter.ai/api/v1",
        timeout: float = 60.0,
    ) -> None:
        self._api_key = api_key or os.getenv("OPENROUTER_API_KEY")
        if not self._api_key:
            raise ValueError(
                "OpenRouterEmbeddingProvider requires OPENROUTER_API_KEY. "
                "Set it in .env or pass api_key=."
            )
        self._model = model
        self._dim = dim
        self._base_url = base_url
        self._timeout = timeout
        self._client = None  # lazy init

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def signature(self) -> Tuple[str, str, int]:
        return ("openrouter", self._model, self._dim)

    def is_available(self) -> bool:
        return bool(self._api_key)

    def _get_client(self):
        if self._client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:
                raise RuntimeError(
                    "openai package is required for OpenRouterEmbeddingProvider"
                ) from exc
            self._client = OpenAI(
                api_key=self._api_key,
                base_url=self._base_url,
                timeout=self._timeout,
                default_headers={
                    # OpenRouter requires these for rate-limit attribution.
                    # They can be any string; the values below identify VegeX.
                    "HTTP-Referer": "https://github.com/tdahq8/vegex",
                    "X-Title": "VegeX",
                },
            )
        return self._client

    def embed(self, text: str) -> List[float]:
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: Sequence[str]) -> List[List[float]]:
        if not texts:
            return []
        client = self._get_client()
        results: List[List[float]] = []
        for i in range(0, len(texts), self.BATCH_SIZE):
            chunk = list(texts[i: i + self.BATCH_SIZE])
            try:
                response = client.embeddings.create(
                    model=self._model,
                    input=chunk,
                    dimensions=self._dim,
                )
                ordered = sorted(response.data, key=lambda d: d.index)
                for item in ordered:
                    results.append(list(item.embedding))
            except Exception as exc:
                logger.warning(
                    "OpenRouter embedding batch %d failed: %s",
                    i // self.BATCH_SIZE, exc,
                )
                # Re-raise — the caller decides whether to skip or abort.
                raise
        return results


# ---------------------------------------------------------------------------
# Local — fastembed (ONNX) offline provider
# ---------------------------------------------------------------------------
class LocalEmbeddingProvider(BaseEmbeddingProvider):
    """
    Offline provider using fastembed (ONNX runtime, ~50MB model download).

    Deliberately avoids `sentence-transformers` (which pulls PyTorch,
    ~2GB). If fastembed is not installed, `is_available()` returns False
    and construction raises — the factory handles fallback.
    """

    DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
    DEFAULT_DIM = 384

    def __init__(self, model: str = DEFAULT_MODEL) -> None:
        try:
            from fastembed import TextEmbedding
        except ImportError as exc:
            raise RuntimeError(
                "fastembed not installed. Install with: pip install '.[embeddings]'"
            ) from exc
        self._model_name = model
        self._dim = self.DEFAULT_DIM
        self._text_embedding_cls = TextEmbedding
        self._engine = None  # lazy

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def signature(self) -> Tuple[str, str, int]:
        return ("local", self._model_name, self._dim)

    def is_available(self) -> bool:
        try:
            from fastembed import TextEmbedding  # noqa: F401
            return True
        except ImportError:
            return False

    def _get_engine(self):
        if self._engine is None:
            self._engine = self._text_embedding_cls(model_name=self._model_name)
        return self._engine

    def embed(self, text: str) -> List[float]:
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: Sequence[str]) -> List[List[float]]:
        if not texts:
            return []
        engine = self._get_engine()
        vectors = list(engine.embed(list(texts)))
        return [list(map(float, v)) for v in vectors]


__all__ = [
    "BaseEmbeddingProvider",
    "HashEmbeddingProvider",
    "OpenRouterEmbeddingProvider",
    "LocalEmbeddingProvider",
]