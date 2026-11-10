"""
SQLite-backed persistent storage for MemoryNode instances.
Source of truth for A-Score signals (B, T, H, V, is_critical, scope, timestamps).
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

from memory.schemas import (
    DimensionMismatchError,
    MemoryNode,
    MemoryNotFoundError,
    MemoryScope,
    MemoryStorageError,
)

logger = logging.getLogger(__name__)


class SQLiteMemoryStorage:
    """Thread-safe SQLite storage for MemoryNode instances."""

    _SCHEMA_VERSION = 2

    _CREATE_TABLE = """
    CREATE TABLE IF NOT EXISTS memory_nodes (
        node_id          TEXT PRIMARY KEY,
        vector           TEXT NOT NULL,
        content          TEXT NOT NULL,
        metadata         TEXT NOT NULL DEFAULT '{}',
        base_trust       REAL NOT NULL DEFAULT 0.3,
        trigger_count    INTEGER NOT NULL DEFAULT 0,
        helpful_count    INTEGER NOT NULL DEFAULT 0,
        violation_count  INTEGER NOT NULL DEFAULT 0,
        is_critical      INTEGER NOT NULL DEFAULT 0,
        scope_type       TEXT NOT NULL DEFAULT 'global',
        scope_value      TEXT NOT NULL DEFAULT '*',
        created_at       REAL NOT NULL,
        last_used_at     REAL NOT NULL
    );
    """

    _CREATE_META_TABLE = """
    CREATE TABLE IF NOT EXISTS schema_meta (
        key        TEXT PRIMARY KEY,
        value      TEXT NOT NULL,
        updated_at REAL NOT NULL
    );
    """

    _CREATE_INDEXES = (
        "CREATE INDEX IF NOT EXISTS idx_mn_created_at ON memory_nodes (created_at);",
        "CREATE INDEX IF NOT EXISTS idx_mn_last_used ON memory_nodes (last_used_at);",
        "CREATE INDEX IF NOT EXISTS idx_mn_scope ON memory_nodes (scope_type, scope_value);",
        "CREATE INDEX IF NOT EXISTS idx_mn_critical ON memory_nodes (is_critical);",
    )

    _MIGRATION_COLUMNS: Tuple[Tuple[str, str], ...] = (
        ("base_trust", "REAL NOT NULL DEFAULT 0.3"),
        ("trigger_count", "INTEGER NOT NULL DEFAULT 0"),
        ("helpful_count", "INTEGER NOT NULL DEFAULT 0"),
        ("violation_count", "INTEGER NOT NULL DEFAULT 0"),
        ("is_critical", "INTEGER NOT NULL DEFAULT 0"),
        ("scope_type", "TEXT NOT NULL DEFAULT 'global'"),
        ("scope_value", "TEXT NOT NULL DEFAULT '*'"),
        ("last_used_at", "REAL NOT NULL DEFAULT 0"),
    )

    _INSERT_SQL = """
    INSERT INTO memory_nodes (
        node_id, vector, content, metadata,
        base_trust, trigger_count, helpful_count, violation_count, is_critical,
        scope_type, scope_value, created_at, last_used_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
    """

    def __init__(
        self,
        db_path: Union[str, Path] = ":memory:",
        expected_dim: Optional[int] = None,
        timeout: float = 30.0,
    ) -> None:
        self.db_path = str(db_path)
        self.expected_dim = expected_dim
        self.timeout = timeout
        self._lock = threading.RLock()
        self._conn: Optional[sqlite3.Connection] = None

        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

        self._initialize()

    def _get_connection(self) -> sqlite3.Connection:
        if self._conn is None:
            try:
                conn = sqlite3.connect(
                    self.db_path, timeout=self.timeout, check_same_thread=False
                )
                conn.row_factory = sqlite3.Row
                if self.db_path != ":memory:":
                    conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute("PRAGMA synchronous=NORMAL;")
                self._conn = conn
            except sqlite3.Error as exc:
                raise MemoryStorageError(
                    f"Failed to open SQLite database at {self.db_path}: {exc}"
                ) from exc
        return self._conn

    def _initialize(self) -> None:
        try:
            with self._lock:
                conn = self._get_connection()
                with conn:
                    conn.execute(self._CREATE_TABLE)
                    conn.execute(self._CREATE_META_TABLE)
                    for idx_sql in self._CREATE_INDEXES:
                        conn.execute(idx_sql)
                self._ensure_schema(conn)
                with conn:
                    conn.execute(f"PRAGMA user_version = {self._SCHEMA_VERSION};")
        except sqlite3.Error as exc:
            raise MemoryStorageError(
                f"Failed to initialize memory storage schema: {exc}"
            ) from exc

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        existing = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(memory_nodes);").fetchall()
        }
        for column, definition in self._MIGRATION_COLUMNS:
            if column in existing:
                continue
            try:
                conn.execute(
                    f"ALTER TABLE memory_nodes ADD COLUMN {column} {definition};"
                )
            except sqlite3.Error as exc:
                raise MemoryStorageError(
                    f"Failed to migrate column '{column}': {exc}"
                ) from exc

    # ------------------------------------------------------------------ #
    # Config
    # ------------------------------------------------------------------ #
    def set_expected_dim(self, dim: Optional[int]) -> None:
        """Update the vector-dimension constraint. Used by the migrator."""
        self.expected_dim = dim

    # ------------------------------------------------------------------ #
    # Validation
    # ------------------------------------------------------------------ #
    def _validate_vector(self, vector: List[float]) -> None:
        if not isinstance(vector, list) or not vector:
            raise MemoryStorageError("Vector must be a non-empty list of floats.")
        if self.expected_dim is not None and len(vector) != self.expected_dim:
            raise DimensionMismatchError(self.expected_dim, len(vector))
        for idx, value in enumerate(vector):
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise MemoryStorageError(
                    f"Vector element at index {idx} is not numeric: {value!r}"
                )
            if math.isnan(value) or math.isinf(value):
                raise MemoryStorageError(
                    f"Vector contains NaN/Inf at index {idx}: {value!r}"
                )

    @staticmethod
    def _serialize_metadata(metadata: dict) -> str:
        try:
            return json.dumps(metadata or {})
        except (TypeError, ValueError) as exc:
            raise MemoryStorageError(
                f"Metadata is not JSON-serializable: {exc}"
            ) from exc

    def _row_to_node(self, row: sqlite3.Row) -> MemoryNode:
        try:
            vector = json.loads(row["vector"])
            metadata = json.loads(row["metadata"]) if row["metadata"] else {}
            scope_type = MemoryScope(row["scope_type"])
        except (TypeError, ValueError) as exc:
            raise MemoryStorageError(
                f"Corrupted row for node_id={row['node_id']!r}: {exc}"
            ) from exc

        return MemoryNode(
            node_id=row["node_id"],
            vector=vector,
            content=row["content"],
            metadata=metadata,
            base_trust=float(row["base_trust"]),
            trigger_count=int(row["trigger_count"]),
            helpful_count=int(row["helpful_count"]),
            violation_count=int(row["violation_count"]),
            is_critical=bool(row["is_critical"]),
            scope_type=scope_type,
            scope_value=row["scope_value"],
            created_at=float(row["created_at"]),
            last_used_at=float(row["last_used_at"]),
        )

    @staticmethod
    def _node_params(node: MemoryNode) -> Tuple:
        return (
            node.node_id,
            json.dumps(node.vector),
            node.content,
            SQLiteMemoryStorage._serialize_metadata(node.metadata),
            float(node.base_trust),
            int(node.trigger_count),
            int(node.helpful_count),
            int(node.violation_count),
            1 if node.is_critical else 0,
            node.scope_type.value,
            node.scope_value,
            float(node.created_at),
            float(node.last_used_at),
        )

    # ------------------------------------------------------------------ #
    # Strict public API
    # ------------------------------------------------------------------ #
    def insert(self, node: MemoryNode) -> None:
        if not isinstance(node, MemoryNode):
            raise MemoryStorageError(
                f"insert() expected MemoryNode, got {type(node).__name__}"
            )
        self._validate_vector(node.vector)
        try:
            with self._lock:
                conn = self._get_connection()
                with conn:
                    conn.execute(self._INSERT_SQL, self._node_params(node))
        except sqlite3.IntegrityError as exc:
            raise MemoryStorageError(
                f"Memory node '{node.node_id}' already exists: {exc}"
            ) from exc
        except sqlite3.Error as exc:
            raise MemoryStorageError(
                f"Failed to insert memory node '{node.node_id}': {exc}"
            ) from exc

    def insert_batch(self, nodes: List[MemoryNode]) -> None:
        if not nodes:
            return
        records = []
        for node in nodes:
            if not isinstance(node, MemoryNode):
                raise MemoryStorageError(
                    f"insert_batch() expected MemoryNode, got {type(node).__name__}"
                )
            self._validate_vector(node.vector)
            records.append(self._node_params(node))
        try:
            with self._lock:
                conn = self._get_connection()
                with conn:
                    conn.executemany(self._INSERT_SQL, records)
        except sqlite3.IntegrityError as exc:
            raise MemoryStorageError(
                f"Batch insert failed due to constraint violation: {exc}"
            ) from exc
        except sqlite3.Error as exc:
            raise MemoryStorageError(f"Batch insert failed: {exc}") from exc

    def replace_all(self, nodes: List[MemoryNode]) -> None:
        """
        Atomically replace the entire node table with `nodes`.
        Used by the migrator. Preserves nothing implicitly — the caller
        must supply fully-formed nodes.
        """
        records = []
        for node in nodes:
            if not isinstance(node, MemoryNode):
                raise MemoryStorageError(
                    f"replace_all() expected MemoryNode, got {type(node).__name__}"
                )
            self._validate_vector(node.vector)
            records.append(self._node_params(node))
        try:
            with self._lock:
                conn = self._get_connection()
                with conn:
                    conn.execute("DELETE FROM memory_nodes;")
                    if records:
                        conn.executemany(self._INSERT_SQL, records)
        except sqlite3.Error as exc:
            raise MemoryStorageError(
                f"Atomic replace_all failed: {exc}"
            ) from exc

    def get(self, node_id: str) -> MemoryNode:
        if not isinstance(node_id, str) or not node_id:
            raise MemoryStorageError("node_id must be a non-empty string.")
        try:
            with self._lock:
                conn = self._get_connection()
                row = conn.execute(
                    "SELECT * FROM memory_nodes WHERE node_id = ?;", (node_id,)
                ).fetchone()
        except sqlite3.Error as exc:
            raise MemoryStorageError(
                f"Failed to retrieve memory node '{node_id}': {exc}"
            ) from exc
        if row is None:
            raise MemoryNotFoundError(node_id)
        return self._row_to_node(row)

    def query(self, vector: List[float], limit: int = 10) -> List[MemoryNode]:
        if limit <= 0:
            return []
        self._validate_vector(vector)
        try:
            with self._lock:
                conn = self._get_connection()
                rows = conn.execute("SELECT * FROM memory_nodes;").fetchall()
        except sqlite3.Error as exc:
            raise MemoryStorageError(f"Failed to scan memory nodes: {exc}") from exc

        query_norm = math.sqrt(sum(v * v for v in vector))
        if query_norm == 0.0:
            return []

        scored: List[Tuple[float, MemoryNode]] = []
        for row in rows:
            node = self._row_to_node(row)
            if len(node.vector) != len(vector):
                continue
            node_norm = math.sqrt(sum(v * v for v in node.vector))
            if node_norm == 0.0:
                continue
            dot = sum(a * b for a, b in zip(vector, node.vector))
            scored.append((dot / (query_norm * node_norm), node))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [node for _, node in scored[:limit]]

    def delete(self, node_id: str) -> bool:
        try:
            with self._lock:
                conn = self._get_connection()
                with conn:
                    cur = conn.execute(
                        "DELETE FROM memory_nodes WHERE node_id = ?;", (node_id,)
                    )
                    return cur.rowcount > 0
        except sqlite3.Error as exc:
            raise MemoryStorageError(
                f"Failed to delete memory node '{node_id}': {exc}"
            ) from exc

    # ------------------------------------------------------------------ #
    # Extended API
    # ------------------------------------------------------------------ #
    def list_all(self) -> List[MemoryNode]:
        try:
            with self._lock:
                conn = self._get_connection()
                rows = conn.execute("SELECT * FROM memory_nodes;").fetchall()
        except sqlite3.Error as exc:
            raise MemoryStorageError(f"Failed to list memory nodes: {exc}") from exc
        return [self._row_to_node(row) for row in rows]

    def query_scoped(
        self,
        scope_type: Optional[MemoryScope] = None,
        scope_value: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[MemoryNode]:
        clauses: List[str] = []
        params: List[object] = []
        if scope_type is not None:
            clauses.append("scope_type = ?")
            params.append(scope_type.value)
        if scope_value is not None:
            clauses.append("scope_value = ?")
            params.append(scope_value)
        sql = "SELECT * FROM memory_nodes"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY last_used_at DESC"
        if limit is not None and limit > 0:
            sql += f" LIMIT {int(limit)}"
        try:
            with self._lock:
                conn = self._get_connection()
                rows = conn.execute(sql, params).fetchall()
        except sqlite3.Error as exc:
            raise MemoryStorageError(f"Failed to query scoped nodes: {exc}") from exc
        return [self._row_to_node(row) for row in rows]

    # ------------------------------------------------------------------ #
    # Metadata (schema_meta)
    # ------------------------------------------------------------------ #
    def get_meta(self, key: str) -> Optional[str]:
        try:
            with self._lock:
                conn = self._get_connection()
                row = conn.execute(
                    "SELECT value FROM schema_meta WHERE key = ?;", (key,)
                ).fetchone()
        except sqlite3.Error as exc:
            raise MemoryStorageError(
                f"Failed to read schema_meta['{key}']: {exc}"
            ) from exc
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        try:
            with self._lock:
                conn = self._get_connection()
                with conn:
                    conn.execute(
                        """
                        INSERT INTO schema_meta (key, value, updated_at)
                        VALUES (?, ?, ?)
                        ON CONFLICT(key) DO UPDATE SET
                            value = excluded.value,
                            updated_at = excluded.updated_at;
                        """,
                        (key, value, time.time()),
                    )
        except sqlite3.Error as exc:
            raise MemoryStorageError(
                f"Failed to write schema_meta['{key}']: {exc}"
            ) from exc

    # ------------------------------------------------------------------ #
    # Feedback loop
    # ------------------------------------------------------------------ #
    def record_trigger(
        self, node_ids: List[str], touched_at: Optional[float] = None
    ) -> int:
        return self._apply_deltas(
            node_ids, trigger_delta=1, helpful_delta=0,
            violation_delta=0, touched_at=touched_at,
        )

    def record_helpful(self, node_ids: List[str]) -> int:
        return self._apply_deltas(
            node_ids, trigger_delta=0, helpful_delta=1, violation_delta=0
        )

    def record_violation(self, node_ids: List[str]) -> int:
        return self._apply_deltas(
            node_ids, trigger_delta=0, helpful_delta=0, violation_delta=1
        )

    def _apply_deltas(
        self,
        node_ids: List[str],
        trigger_delta: int,
        helpful_delta: int,
        violation_delta: int,
        touched_at: Optional[float] = None,
    ) -> int:
        if not node_ids:
            return 0
        ts = time.time() if touched_at is None else float(touched_at)
        affected = 0
        try:
            with self._lock:
                conn = self._get_connection()
                with conn:
                    for node_id in node_ids:
                        cur = conn.execute(
                            """
                            UPDATE memory_nodes
                            SET trigger_count   = trigger_count   + ?,
                                helpful_count   = helpful_count   + ?,
                                violation_count = violation_count + ?,
                                last_used_at    = ?
                            WHERE node_id = ?;
                            """,
                            (trigger_delta, helpful_delta, violation_delta, ts, node_id),
                        )
                        affected += cur.rowcount
        except sqlite3.Error as exc:
            raise MemoryStorageError(
                f"Failed to apply feedback deltas: {exc}"
            ) from exc
        return affected

    def promote(self, node_id: str, base_trust: float = 1.0) -> None:
        if not 0.0 <= base_trust <= 1.0:
            raise MemoryStorageError(
                f"base_trust must be within [0.0, 1.0], got {base_trust!r}"
            )
        try:
            with self._lock:
                conn = self._get_connection()
                with conn:
                    cur = conn.execute(
                        """
                        UPDATE memory_nodes
                        SET base_trust = ?, is_critical = 1
                        WHERE node_id = ?;
                        """,
                        (float(base_trust), node_id),
                    )
                    if cur.rowcount == 0:
                        raise MemoryNotFoundError(node_id)
        except MemoryNotFoundError:
            raise
        except sqlite3.Error as exc:
            raise MemoryStorageError(
                f"Failed to promote memory node '{node_id}': {exc}"
            ) from exc

    def count(self) -> int:
        try:
            with self._lock:
                conn = self._get_connection()
                row = conn.execute("SELECT COUNT(*) FROM memory_nodes;").fetchone()
                return int(row[0]) if row else 0
        except sqlite3.Error as exc:
            raise MemoryStorageError(f"Failed to count memory nodes: {exc}") from exc

    def clear(self) -> None:
        try:
            with self._lock:
                conn = self._get_connection()
                with conn:
                    conn.execute("DELETE FROM memory_nodes;")
        except sqlite3.Error as exc:
            raise MemoryStorageError(f"Failed to clear memory store: {exc}") from exc

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except sqlite3.Error:
                    pass
                finally:
                    self._conn = None

    def __enter__(self) -> "SQLiteMemoryStorage":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()