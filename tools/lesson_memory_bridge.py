"""
tools/lesson_memory_bridge.py
------------------------------
Bridges the JSON-file lesson system with the SQLite-backed A-Score memory engine.

Design:
  - lessons/*.json  → source of truth (content, human-editable, git-tracked)
  - .differix/lessons.db → regenerable ranking index (vectors, T/H/V stats)

Embedding providers are selected via `build_embedding_provider(config)`.
On bridge initialization, `check_and_migrate` ensures the storage's vector
dimension and provider signature match the active provider. If they differ,
existing rows are re-embedded from their `content` field, preserving all
A-Score signals.

Public API:
  - LessonMemoryBridge(lessons_root, db_path, ...)
      .sync_all(force=False) -> SyncReport
      .search(query, limit, stages, scope_type, scope_value, record_trigger) -> List[ScoredMemoryNode]
      .record_helpful(node_ids) -> int
      .record_violation(node_ids) -> int
      .stats() -> dict
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from memory import (
    BaseEmbeddingProvider,
    MemoryManager,
    MemoryNode,
    MemoryScope,
    RankingWeights,
    ScoredMemoryNode,
    SQLiteMemoryStorage,
    build_embedding_provider,
    check_and_migrate,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sync report
# ---------------------------------------------------------------------------
@dataclass
class SyncReport:
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    deleted: int = 0
    errors: List[str] = field(default_factory=list)
    elapsed_ms: float = 0.0
    migration: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "inserted": self.inserted,
            "updated": self.updated,
            "skipped": self.skipped,
            "deleted": self.deleted,
            "errors": list(self.errors),
            "elapsed_ms": round(self.elapsed_ms, 2),
            "migration": self.migration,
        }


# ---------------------------------------------------------------------------
# Bridge
# ---------------------------------------------------------------------------
class LessonMemoryBridge:
    """Synchronizes lessons/*/*.json into a MemoryManager SQLite index."""

    STAGES: Tuple[str, ...] = ("observations", "candidates", "promoted", "deprecated")

    STAGE_TRUST: Dict[str, float] = {
        "observations": 0.3,
        "candidates": 0.5,
        "promoted": 1.0,
        "deprecated": 0.1,
    }

    STAGE_CRITICAL: Dict[str, bool] = {
        "observations": False,
        "candidates": False,
        "promoted": True,
        "deprecated": False,
    }

    def __init__(
        self,
        lessons_root: str | os.PathLike,
        db_path: str | os.PathLike,
        embedding_provider: Optional[BaseEmbeddingProvider] = None,
        ranking_weights: Optional[RankingWeights] = None,
        config_module: Optional[Any] = None,
    ) -> None:
        self.lessons_root = Path(lessons_root).resolve()
        self.db_path = Path(db_path).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # Provider selection: explicit override, else factory.
        if embedding_provider is not None:
            self.embedder = embedding_provider
        else:
            if config_module is None:
                import config as config_module  # type: ignore
            self.embedder = build_embedding_provider(config_module)

        # Storage is created with the active provider's dim.
        storage = SQLiteMemoryStorage(
            db_path=str(self.db_path),
            expected_dim=self.embedder.dim,
        )

        # Migrate if the on-disk signature differs (re-embeds all rows).
        self._migration_report = check_and_migrate(storage, self.embedder)
        if self._migration_report.action == "migrated":
            logger.info("Bridge migration: %s", self._migration_report)
        elif self._migration_report.action == "aborted":
            logger.error("Bridge migration aborted: %s", self._migration_report.error)

        self.manager = MemoryManager(
            storage=storage,
            default_weights=ranking_weights or RankingWeights(),
        )

    # ------------------------------------------------------------------ #
    # Sync
    # ------------------------------------------------------------------ #
    def sync_all(self, force: bool = False) -> SyncReport:
        t0 = time.perf_counter()
        report = SyncReport()
        report.migration = str(self._migration_report)

        seen_ids: set = set()
        json_by_stage: Dict[str, List[Path]] = {s: [] for s in self.STAGES}

        for stage in self.STAGES:
            stage_dir = self.lessons_root / stage
            if not stage_dir.is_dir():
                continue
            for fp in sorted(stage_dir.glob("*.json")):
                json_by_stage[stage].append(fp)

        for stage, paths in json_by_stage.items():
            for path in paths:
                try:
                    node = self._sync_one(stage, path, force=force, report=report)
                    if node is not None:
                        seen_ids.add(node.node_id)
                except Exception as exc:
                    report.errors.append(f"{stage}/{path.name}: {exc}")

        report.deleted = self._purge_orphans(seen_ids)
        report.elapsed_ms = (time.perf_counter() - t0) * 1000.0
        return report

    def _sync_one(
        self,
        stage: str,
        path: Path,
        force: bool,
        report: SyncReport,
    ) -> Optional[MemoryNode]:
        raw = path.read_bytes()
        content_hash = hashlib.sha256(raw).hexdigest()

        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            report.errors.append(f"{stage}/{path.name}: invalid JSON ({exc})")
            return None

        lesson_id = str(data.get("id") or path.stem).strip()
        if not lesson_id:
            report.errors.append(f"{stage}/{path.name}: missing 'id'")
            return None

        node_id = f"lesson:{lesson_id}"

        existing: Optional[MemoryNode] = None
        try:
            existing = self.manager.get(node_id)
        except Exception:
            existing = None

        # Fast path: unchanged content + same stage → skip
        if existing is not None and not force:
            prev_hash = (existing.metadata or {}).get("content_hash")
            prev_stage = (existing.metadata or {}).get("stage")
            if prev_hash == content_hash and prev_stage == stage:
                report.skipped += 1
                return existing

        node = self._build_node(stage, path, data, content_hash, existing)
        if existing is None:
            self.manager.add(node)
            report.inserted += 1
        else:
            # Delete+reinsert to preserve runtime signals from `node`
            self.manager.storage.delete(node.node_id)
            self.manager.add(node)
            report.updated += 1
        return node

    def _build_node(
        self,
        stage: str,
        path: Path,
        data: Dict[str, Any],
        content_hash: str,
        existing: Optional[MemoryNode],
    ) -> MemoryNode:
        lesson_id = str(data.get("id") or path.stem).strip()
        content_text = self._render_content_text(data)
        vector = self.embedder.embed(content_text)

        now = time.time()
        trigger = existing.trigger_count if existing else 0
        helpful = existing.helpful_count if existing else 0
        violation = existing.violation_count if existing else 0
        last_used = existing.last_used_at if existing else now
        created_at = existing.created_at if existing else now

        rel_path = path.relative_to(self.lessons_root).as_posix()

        metadata: Dict[str, Any] = {
            "stage": stage,
            "lesson_id": lesson_id,
            "lesson_file": rel_path,
            "content_hash": content_hash,
            "root_cause": str(data.get("root_cause", "")),
            "details": data.get("details", {}),
            "tags": list(data.get("tags", []) or []),
            "timestamp": data.get("timestamp"),
        }

        return MemoryNode(
            node_id=f"lesson:{lesson_id}",
            vector=vector,
            content=content_text,
            metadata=metadata,
            base_trust=self.STAGE_TRUST.get(stage, 0.3),
            trigger_count=trigger,
            helpful_count=helpful,
            violation_count=violation,
            is_critical=self.STAGE_CRITICAL.get(stage, False),
            scope_type=MemoryScope.PROJECT,
            scope_value="differix",
            created_at=created_at,
            last_used_at=last_used,
        )

    @staticmethod
    def _render_content_text(data: Dict[str, Any]) -> str:
        parts: List[str] = []
        root_cause = str(data.get("root_cause", "")).strip()
        if root_cause:
            parts.append(f"Root cause: {root_cause}")

        details = data.get("details", {})
        if isinstance(details, dict):
            for k, v in details.items():
                sv = str(v).strip()
                if sv:
                    parts.append(f"{k}: {sv}")
        elif details:
            parts.append(str(details))

        tags = data.get("tags", [])
        if tags:
            parts.append("Tags: " + ", ".join(str(t) for t in tags))

        text = "\n".join(parts).strip()
        return text or str(data.get("id", "empty lesson"))

    def _purge_orphans(self, live_node_ids: set) -> int:
        deleted = 0
        try:
            all_nodes = self.manager.storage.list_all()
        except Exception as exc:
            logger.warning("Failed to list nodes during purge: %s", exc)
            return 0
        for node in all_nodes:
            if not node.node_id.startswith("lesson:"):
                continue
            if node.node_id not in live_node_ids:
                try:
                    self.manager.storage.delete(node.node_id)
                    deleted += 1
                except Exception as exc:
                    logger.warning("Failed to purge node %s: %s", node.node_id, exc)
        return deleted

    # ------------------------------------------------------------------ #
    # Retrieval
    # ------------------------------------------------------------------ #
    def search(
        self,
        query: str,
        limit: int = 5,
        stages: Optional[Sequence[str]] = None,
        scope_type: MemoryScope = MemoryScope.PROJECT,
        scope_value: str = "differix",
        record_trigger: bool = True,
    ) -> List[ScoredMemoryNode]:
        if not query or not query.strip():
            return []

        try:
            qvec = self.embedder.embed(query)
        except Exception as exc:
            logger.warning("Query embedding failed: %s", exc)
            return []

        all_nodes = self.manager.storage.list_all()
        if stages is not None:
            stage_set = set(stages)
            candidates = [
                n for n in all_nodes
                if n.node_id.startswith("lesson:")
                and (n.metadata or {}).get("stage") in stage_set
            ]
        else:
            candidates = [n for n in all_nodes if n.node_id.startswith("lesson:")]

        if not candidates:
            return []

        from memory import rank_memory_nodes_scored

        scored = rank_memory_nodes_scored(
            nodes=candidates,
            query_vector=qvec,
            weights=self.manager.weights,
            query_scope=scope_type,
            query_scope_value=scope_value,
            top_k=limit,
        )

        if record_trigger and scored:
            node_ids = [s.node.node_id for s in scored]
            try:
                self.manager.record_trigger(node_ids)
                for s in scored:
                    s.node.trigger_count += 1
                    s.node.last_used_at = time.time()
            except Exception as exc:
                logger.warning("Failed to persist trigger feedback: %s", exc)

        return scored

    # ------------------------------------------------------------------ #
    # Feedback loop
    # ------------------------------------------------------------------ #
    def record_helpful(self, node_ids: Iterable[str]) -> int:
        ids = [i for i in node_ids if i]
        return self.manager.record_helpful(ids) if ids else 0

    def record_violation(self, node_ids: Iterable[str]) -> int:
        ids = [i for i in node_ids if i]
        return self.manager.record_violation(ids) if ids else 0

    # ------------------------------------------------------------------ #
    # Stats
    # ------------------------------------------------------------------ #
    def stats(self) -> Dict[str, Any]:
        all_nodes = self.manager.storage.list_all()
        by_stage: Dict[str, int] = {s: 0 for s in self.STAGES}
        for n in all_nodes:
            if not n.node_id.startswith("lesson:"):
                continue
            stage = (n.metadata or {}).get("stage", "unknown")
            by_stage[stage] = by_stage.get(stage, 0) + 1
        return {
            "db_path": str(self.db_path),
            "embedding_signature": list(self.embedder.signature),
            "embedding_dim": self.embedder.dim,
            "migration_status": str(self._migration_report),
            "total_lesson_nodes": sum(by_stage.values()),
            "by_stage": by_stage,
        }

    def close(self) -> None:
        self.manager.close()


__all__ = ["LessonMemoryBridge", "SyncReport"]