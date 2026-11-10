"""
Vector migration across embedding provider changes.

When the active provider's signature differs from what's recorded in the
database (or when no signature is recorded but rows already exist), existing
vectors are incompatible with new queries. This module re-embeds every
stored node from its `content` field and swaps them atomically, preserving
all A-Score signals (base_trust, trigger_count, helpful_count,
violation_count, is_critical, scope, timestamps).

Public API:
    check_and_migrate(storage, provider) -> MigrationReport

Design:
  - Re-embedding happens BEFORE any write, so a failing provider leaves
    the DB untouched (the caller can retry with a different provider).
  - The swap uses `storage.replace_all()` which is transactional at the
    SQLite layer.
  - If there are existing rows and no signature, we MUST re-embed — the
    vector dimensions of pre-signature rows are unknown. This closes the
    failure mode where a fresh schema_meta table caused old vectors to be
    silently accepted as if they matched a new provider.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

from memory.embeddings import BaseEmbeddingProvider
from memory.schemas import MemoryNode, MemoryStorageError
from memory.storage import SQLiteMemoryStorage

logger = logging.getLogger(__name__)


_SIGNATURE_KEY = "embedding_signature"


@dataclass
class MigrationReport:
    action: str  # "init" | "none" | "migrated" | "aborted"
    rows_migrated: int = 0
    old_signature: Optional[Tuple[str, str, int]] = None
    new_signature: Optional[Tuple[str, str, int]] = None
    elapsed_ms: float = 0.0
    error: Optional[str] = None

    def __str__(self) -> str:
        if self.action == "none":
            return f"none (already {self.new_signature})"
        if self.action == "init":
            return f"init → {self.new_signature}"
        if self.action == "migrated":
            return (
                f"migrated {self.rows_migrated} rows: "
                f"{self.old_signature} → {self.new_signature} "
                f"in {self.elapsed_ms:.0f}ms"
            )
        return f"aborted: {self.error}"


def _serialize_signature(sig: Tuple[str, str, int]) -> str:
    return f"{sig[0]}::{sig[1]}::{sig[2]}"


def _deserialize_signature(raw: str) -> Optional[Tuple[str, str, int]]:
    try:
        parts = raw.split("::")
        if len(parts) != 3:
            return None
        return (parts[0], parts[1], int(parts[2]))
    except (ValueError, IndexError):
        return None


def check_and_migrate(
    storage: SQLiteMemoryStorage,
    provider: BaseEmbeddingProvider,
) -> MigrationReport:
    """
    Ensure the storage contains vectors compatible with `provider`.

    Four outcomes:
      init      — fresh DB with no rows, signature recorded, no re-embed
      none      — DB signature matches active provider, no-op
      migrated  — rows re-embedded and swapped atomically
      aborted   — re-embedding failed; DB untouched, next call retries
    """
    t0 = time.perf_counter()
    new_sig = provider.signature

    raw = storage.get_meta(_SIGNATURE_KEY)
    stored_sig = _deserialize_signature(raw) if raw else None

    # ---- Case 1: fresh DB with no rows and no signature ------------------
    if stored_sig is None:
        try:
            row_count = storage.count()
        except MemoryStorageError as exc:
            return MigrationReport(
                action="aborted",
                new_signature=new_sig,
                error=f"Failed to count rows: {exc}",
                elapsed_ms=(time.perf_counter() - t0) * 1000.0,
            )

        if row_count == 0:
            storage.set_meta(_SIGNATURE_KEY, _serialize_signature(new_sig))
            return MigrationReport(
                action="init",
                new_signature=new_sig,
                elapsed_ms=(time.perf_counter() - t0) * 1000.0,
            )

        # ---- Case 2: rows exist but no signature was ever recorded --------
        # The rows' vector dimensions are unknown; we cannot trust them to
        # match the new provider. Re-embed all of them.
        logger.warning(
            "Found %d pre-existing rows with no recorded signature. "
            "Re-embedding all rows with the active provider (%s).",
            row_count,
            new_sig,
        )
        return _reembed_and_swap(
            storage=storage,
            provider=provider,
            new_sig=new_sig,
            old_sig=None,
            reason="no-signature-with-rows",
            start_time=t0,
        )

    # ---- Case 3: signature matches — no-op ------------------------------
    if stored_sig == new_sig:
        return MigrationReport(
            action="none",
            old_signature=stored_sig,
            new_signature=new_sig,
            elapsed_ms=(time.perf_counter() - t0) * 1000.0,
        )

    # ---- Case 4: signature mismatch — re-embed --------------------------
    logger.warning(
        "Embedding signature changed: %s → %s. Re-embedding all rows.",
        stored_sig,
        new_sig,
    )
    return _reembed_and_swap(
        storage=storage,
        provider=provider,
        new_sig=new_sig,
        old_sig=stored_sig,
        reason="signature-mismatch",
        start_time=t0,
    )


def _reembed_and_swap(
    storage: SQLiteMemoryStorage,
    provider: BaseEmbeddingProvider,
    new_sig: Tuple[str, str, int],
    old_sig: Optional[Tuple[str, str, int]],
    reason: str,
    start_time: float,
) -> MigrationReport:
    """
    Shared re-embedding logic for both mismatch and pre-existing-rows cases.
    """
    try:
        existing_nodes: List[MemoryNode] = storage.list_all()
    except MemoryStorageError as exc:
        return MigrationReport(
            action="aborted",
            old_signature=old_sig,
            new_signature=new_sig,
            error=f"Failed to read existing nodes: {exc}",
            elapsed_ms=(time.perf_counter() - start_time) * 1000.0,
        )

    if not existing_nodes:
        # Rows disappeared between count and list — treat as init.
        storage.set_meta(_SIGNATURE_KEY, _serialize_signature(new_sig))
        return MigrationReport(
            action="init",
            old_signature=old_sig,
            new_signature=new_sig,
            elapsed_ms=(time.perf_counter() - start_time) * 1000.0,
        )

    # Re-embed everything BEFORE touching the DB. A failing provider must
    # not leave the storage in a half-migrated state.
    try:
        contents = [n.content for n in existing_nodes]
        new_vectors = provider.embed_batch(contents)
        if len(new_vectors) != len(existing_nodes):
            raise RuntimeError(
                f"Provider returned {len(new_vectors)} vectors "
                f"for {len(existing_nodes)} inputs"
            )
        # Validate each returned vector has the expected dimension
        for i, vec in enumerate(new_vectors):
            if not isinstance(vec, list) or len(vec) != provider.dim:
                raise RuntimeError(
                    f"Provider returned vector {i} with shape "
                    f"{len(vec) if isinstance(vec, list) else type(vec).__name__}, "
                    f"expected {provider.dim}"
                )
    except Exception as exc:
        logger.error("Re-embedding failed (%s): %s", reason, exc)
        return MigrationReport(
            action="aborted",
            old_signature=old_sig,
            new_signature=new_sig,
            error=f"Re-embedding failed: {exc}",
            elapsed_ms=(time.perf_counter() - start_time) * 1000.0,
        )

    # Build the new node set — only `vector` changes.
    migrated_nodes: List[MemoryNode] = []
    for node, new_vec in zip(existing_nodes, new_vectors):
        migrated_nodes.append(
            node.model_copy(update={"vector": list(new_vec)})
        )

    # Atomic swap. Update the storage's dimension constraint first so
    # `_validate_vector` accepts the new length.
    storage.set_expected_dim(provider.dim)
    try:
        storage.replace_all(migrated_nodes)
        storage.set_meta(_SIGNATURE_KEY, _serialize_signature(new_sig))
    except MemoryStorageError as exc:
        # Best effort: keep the DB as-is. The next run retries.
        return MigrationReport(
            action="aborted",
            old_signature=old_sig,
            new_signature=new_sig,
            error=f"Atomic swap failed: {exc}",
            elapsed_ms=(time.perf_counter() - start_time) * 1000.0,
        )

    return MigrationReport(
        action="migrated",
        rows_migrated=len(migrated_nodes),
        old_signature=old_sig,
        new_signature=new_sig,
        elapsed_ms=(time.perf_counter() - start_time) * 1000.0,
    )


__all__ = ["check_and_migrate", "MigrationReport"]