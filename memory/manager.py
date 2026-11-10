"""
Unified MemoryManager: orchestrates SQLiteMemoryStorage + A-Score ranker,
exposes the feedback loop (trigger / helpful / violation / promote).
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Dict, List, Optional

from memory.ranker import rank_memory_nodes_scored
from memory.schemas import (
    MemoryNode,
    MemoryScope,
    MemoryStorageError,
    RankingWeights,
    ScoredMemoryNode,
)
from memory.storage import SQLiteMemoryStorage

logger = logging.getLogger(__name__)


class MemoryManager:
    """High-level façade for lesson / memory node management."""

    def __init__(
        self,
        storage: Optional[SQLiteMemoryStorage] = None,
        db_path: str = ":memory:",
        expected_dim: Optional[int] = None,
        default_weights: Optional[RankingWeights] = None,
    ) -> None:
        if storage is not None:
            if not isinstance(storage, SQLiteMemoryStorage):
                raise MemoryStorageError(
                    f"storage must be a SQLiteMemoryStorage, "
                    f"got {type(storage).__name__}"
                )
            self.storage: SQLiteMemoryStorage = storage
        else:
            self.storage = SQLiteMemoryStorage(
                db_path=db_path, expected_dim=expected_dim
            )
        self.weights: RankingWeights = default_weights or RankingWeights()

    # ------------------------------------------------------------------ #
    # Writes
    # ------------------------------------------------------------------ #
    def add(self, node: MemoryNode) -> None:
        self.storage.insert(node)

    def add_batch(self, nodes: List[MemoryNode]) -> None:
        self.storage.insert_batch(nodes)

    def create(
        self,
        content: str,
        vector: List[float],
        metadata: Optional[Dict[str, Any]] = None,
        node_id: Optional[str] = None,
        base_trust: float = 0.3,
        is_critical: bool = False,
        scope_type: MemoryScope = MemoryScope.GLOBAL,
        scope_value: str = "*",
        created_at: Optional[float] = None,
    ) -> MemoryNode:
        now = time.time()
        node = MemoryNode(
            node_id=node_id if node_id is not None else str(uuid.uuid4()),
            vector=vector,
            content=content,
            metadata=metadata or {},
            base_trust=base_trust,
            is_critical=is_critical,
            scope_type=scope_type,
            scope_value=scope_value,
            created_at=created_at if created_at is not None else now,
            last_used_at=created_at if created_at is not None else now,
        )
        self.storage.insert(node)
        return node

    # ------------------------------------------------------------------ #
    # Reads
    # ------------------------------------------------------------------ #
    def get(self, node_id: str) -> MemoryNode:
        return self.storage.get(node_id)

    def list_all(self) -> List[MemoryNode]:
        return self.storage.list_all()

    def count(self) -> int:
        return self.storage.count()

    # ------------------------------------------------------------------ #
    # Retrieval
    # ------------------------------------------------------------------ #
    def search(
        self,
        query_vector: List[float],
        limit: int = 10,
        query_scope: Optional[MemoryScope] = None,
        query_scope_value: Optional[str] = None,
        weights: Optional[RankingWeights] = None,
        candidate_pool: Optional[int] = None,
        record_trigger: bool = True,
        reference_time: Optional[float] = None,
    ) -> List[ScoredMemoryNode]:
        if limit <= 0:
            return []
        pool = candidate_pool if candidate_pool is not None else max(limit * 4, 100)
        candidates = self.storage.list_all()
        if not candidates:
            return []

        eff_weights = weights or self.weights
        scored = rank_memory_nodes_scored(
            nodes=candidates,
            query_vector=query_vector,
            weights=eff_weights,
            query_scope=query_scope,
            query_scope_value=query_scope_value,
            top_k=pool,
            reference_time=reference_time,
        )
        top = scored[:limit]

        if record_trigger and top:
            ids = [s.node.node_id for s in top]
            ts = time.time() if reference_time is None else float(reference_time)
            try:
                self.storage.record_trigger(ids, touched_at=ts)
                for s in top:
                    s.node.trigger_count += 1
                    s.node.last_used_at = ts
            except MemoryStorageError as exc:
                logger.warning(
                    "Failed to persist trigger feedback for %d nodes: %s",
                    len(ids), exc,
                )
        return top

    # ------------------------------------------------------------------ #
    # Feedback loop
    # ------------------------------------------------------------------ #
    def record_trigger(
        self, node_ids: List[str], touched_at: Optional[float] = None
    ) -> int:
        """Increment T (trigger_count) and refresh last_used_at for each node."""
        return self.storage.record_trigger(node_ids, touched_at=touched_at)

    def record_helpful(self, node_ids: List[str]) -> int:
        """Increment H (helpful_count) for each node."""
        return self.storage.record_helpful(node_ids)

    def record_violation(self, node_ids: List[str]) -> int:
        """Increment V (violation_count) for each node."""
        return self.storage.record_violation(node_ids)

    def promote(self, node_id: str, base_trust: float = 1.0) -> None:
        """Human promotion: raise B and mark critical (decay-resistant)."""
        self.storage.promote(node_id, base_trust=base_trust)

    # ------------------------------------------------------------------ #
    # Deletes / lifecycle
    # ------------------------------------------------------------------ #
    def delete(self, node_id: str) -> bool:
        return self.storage.delete(node_id)

    def clear(self) -> None:
        self.storage.clear()

    def close(self) -> None:
        self.storage.close()

    def __enter__(self) -> "MemoryManager":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()


__all__ = ["MemoryManager"]