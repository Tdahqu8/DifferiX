"""
Embedding provider package for the VegeX memory subsystem.

Providers implement a minimal interface:
    .dim        -> int
    .signature  -> (name, model, dim)
    .embed(text) -> list[float]
    .embed_batch(texts) -> list[list[float]]

Use `build_embedding_provider(config)` to select one. Never call provider
classes directly from storage or ranking code — those layers only care
about vector length.
"""

from memory.embeddings.providers import (
    BaseEmbeddingProvider,
    HashEmbeddingProvider,
    LocalEmbeddingProvider,
    OpenRouterEmbeddingProvider,
)
from memory.embeddings.factory import build_embedding_provider

__all__ = [
    "BaseEmbeddingProvider",
    "HashEmbeddingProvider",
    "LocalEmbeddingProvider",
    "OpenRouterEmbeddingProvider",
    "build_embedding_provider",
]