"""
Provider selection with health probing.

Priority order for `auto`:
  1. OpenRouter (if OPENROUTER_API_KEY is set and prefer_local is False)
  2. Local (fastembed, if installed)
  3. Hash (zero-dependency fallback)

Every candidate is probed with a cheap embed call before being accepted.
A broken or placeholder key never gets past the probe.
"""

from __future__ import annotations

import logging
from typing import Optional

from memory.embeddings.providers import (
    BaseEmbeddingProvider,
    HashEmbeddingProvider,
    LocalEmbeddingProvider,
    OpenRouterEmbeddingProvider,
)

logger = logging.getLogger(__name__)

_PROBE_STRING = "vegex embedding probe"


def _probe(provider: BaseEmbeddingProvider) -> bool:
    """
    Return True iff `provider.embed()` succeeds on a trivial input AND
    returns a vector of the expected dimension.
    """
    try:
        vec = provider.embed(_PROBE_STRING)
    except Exception as exc:
        logger.warning(
            "Provider %s failed probe: %s",
            getattr(provider, "signature", "unknown"),
            exc,
        )
        return False

    if not isinstance(vec, list) or len(vec) != provider.dim:
        logger.warning(
            "Provider %s returned vector of unexpected shape: "
            "expected %d, got %s",
            getattr(provider, "signature", "unknown"),
            provider.dim,
            len(vec) if isinstance(vec, list) else type(vec).__name__,
        )
        return False

    return True


def build_embedding_provider(
    config,
    prefer_local: Optional[bool] = None,
    force_provider: Optional[str] = None,
    skip_probe: bool = False,
) -> BaseEmbeddingProvider:
    """
    Build and probe the embedding provider for this process.

    Args:
        config: the `config` module (or any object with the same attributes).
        prefer_local: override config.EMBEDDING_PREFER_LOCAL.
        force_provider: "openrouter" | "local" | "hash" — raises if
            unavailable.
        skip_probe: bypass the health check (testing only).
    """
    provider_choice = (
        force_provider
        or getattr(config, "EMBEDDING_PROVIDER", "auto")
    ).lower()

    if prefer_local is None:
        prefer_local = bool(getattr(config, "EMBEDDING_PREFER_LOCAL", False))

    dim = int(getattr(config, "EMBEDDING_DIM", 512))
    api_key = getattr(config, "OPENROUTER_API_KEY", None)
    model = getattr(
        config, "OPENROUTER_EMBEDDING_MODEL", "openai/text-embedding-3-small"
    )

    # ------------------------------------------------------------------ #
    # Explicit provider
    # ------------------------------------------------------------------ #
    if provider_choice == "hash":
        provider = HashEmbeddingProvider(dim=dim)
        if not skip_probe and not _probe(provider):
            raise RuntimeError("HashEmbeddingProvider failed its own probe.")
        logger.info("Embedding provider forced: hash (%s)", provider.signature)
        return provider

    if provider_choice == "local":
        try:
            provider = LocalEmbeddingProvider()
        except Exception as exc:
            raise RuntimeError(
                f"provider='local' requested but LocalEmbeddingProvider "
                f"could not be constructed: {exc}. "
                f"Install with: pip install '.[embeddings]'"
            ) from exc
        if not skip_probe and not _probe(provider):
            raise RuntimeError(
                "provider='local' requested but the probe failed. "
                "Check fastembed installation and model download access."
            )
        logger.info("Embedding provider forced: local (%s)", provider.signature)
        return provider

    if provider_choice in ("openrouter", "openai"):
        if not api_key:
            raise RuntimeError(
                f"provider='{provider_choice}' requested but "
                f"OPENROUTER_API_KEY is not set."
            )
        provider = OpenRouterEmbeddingProvider(
            api_key=api_key, model=model, dim=dim
        )
        if not skip_probe and not _probe(provider):
            raise RuntimeError(
                f"provider='{provider_choice}' requested but the probe "
                f"failed. Verify OPENROUTER_API_KEY is valid at "
                f"https://openrouter.ai/keys"
            )
        logger.info("Embedding provider forced: openrouter (%s)", provider.signature)
        return provider

    # ------------------------------------------------------------------ #
    # auto — probe each tier in priority order
    # ------------------------------------------------------------------ #
    if api_key and not prefer_local:
        try:
            provider = OpenRouterEmbeddingProvider(
                api_key=api_key, model=model, dim=dim
            )
            if skip_probe or _probe(provider):
                logger.info(
                    "Embedding provider (auto): openrouter (%s)",
                    provider.signature,
                )
                return provider
            logger.warning(
                "OpenRouter provider failed probe — falling back. "
                "Check that OPENROUTER_API_KEY is valid and that your "
                "account has access to openai/text-embedding-3-small."
            )
        except Exception as exc:
            logger.warning("OpenRouter provider init failed: %s", exc)

    try:
        provider = LocalEmbeddingProvider()
        if skip_probe or _probe(provider):
            logger.info(
                "Embedding provider (auto): local (%s)", provider.signature
            )
            return provider
        logger.warning("Local provider failed probe.")
    except Exception as exc:
        logger.debug("Local provider unavailable: %s", exc)

    logger.warning(
        "No working semantic embedding provider available. "
        "Falling back to HashEmbeddingProvider (dim=%d). "
        "Retrieval quality will be limited.",
        dim,
    )
    fallback = HashEmbeddingProvider(dim=dim)
    if not skip_probe and not _probe(fallback):
        raise RuntimeError("HashEmbeddingProvider failed its own probe.")
    return fallback


__all__ = ["build_embedding_provider"]