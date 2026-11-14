"""
tools/atomic_changeset.py
--------------------------
Atomic multi-file change sets for the DifferiX editor pipeline.

Root problem this solves: the RSI crash you hit was caused by multi-file
edits applied non-atomically — file #1 written, file #3 fails sandbox
validation, repo left in a half-edited state. The editor then re-reads the
half-applied tree and hallucinates a fix.

This module makes N-file edits behave as either fully-applied or
fully-rolled-back. On POSIX we can't rename N files in a single syscall,
but we can:
  1. Stage all content to .differix/staging/<uuid>/
  2. Snapshot backups of every file to be touched
  3. Apply replacements one by one (SKIPPING idempotent writes)
  4. On any failure, restore from the backups

Idempotency (Phase 1.2):
  If `content == existing content`, we skip the write, append to `skipped`,
  and never touch disk. This lets the pipeline distinguish
  "no-op" from "wrote something new" and prevents infinite retry loops
  when the LLM generates byte-identical code.
"""

from __future__ import annotations

import errno
import logging
import os
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)


class ChangeSetError(Exception):
    """Raised when a change set cannot be applied."""


@dataclass
class ChangeOp:
    """A single file operation inside a ChangeSet."""

    kind: str  # "write" | "delete"
    path: str  # relative to project_root, forward slashes
    content: Optional[str] = None  # required for kind="write"

    def __post_init__(self) -> None:
        if self.kind not in ("write", "delete"):
            raise ValueError(f"Invalid kind: {self.kind!r}")
        if self.kind == "write" and self.content is None:
            raise ValueError(f"ChangeOp write requires content (path={self.path!r})")
        if not self.path or self.path.startswith("/") or ".." in Path(self.path).parts:
            raise ValueError(f"Path must be project-relative, no traversal: {self.path!r}")


@dataclass
class ApplyReport:
    ok: bool = False
    applied: List[str] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)  # Phase 1.2: idempotent no-ops
    rolled_back: List[str] = field(default_factory=list)
    error: Optional[str] = None
    elapsed_ms: float = 0.0

    def as_dict(self) -> Dict:
        return {
            "ok": self.ok,
            "applied": list(self.applied),
            "skipped": list(self.skipped),
            "rolled_back": list(self.rolled_back),
            "error": self.error,
            "elapsed_ms": round(self.elapsed_ms, 3),
        }


class ChangeSet:
    """A list of file operations applied atomically (best-effort)."""

    def __init__(self, ops: Optional[Iterable[ChangeOp]] = None) -> None:
        self.ops: List[ChangeOp] = list(ops or [])

    def add_write(self, path: str, content: str) -> "ChangeSet":
        self.ops.append(ChangeOp(kind="write", path=path, content=content))
        return self

    def add_delete(self, path: str) -> "ChangeSet":
        self.ops.append(ChangeOp(kind="delete", path=path))
        return self

    def __bool__(self) -> bool:
        return bool(self.ops)

    def __len__(self) -> int:
        return len(self.ops)

    # ------------------------------------------------------------------ #
    # Pre-flight
    # ------------------------------------------------------------------ #
    def validate(self, project_root: str) -> None:
        """
        Raise ChangeSetError if any op violates invariants:
          - path escapes project_root
          - duplicate path within the set
          - write content exceeds size cap (2 MB default)
        """
        root = Path(project_root).resolve()
        seen: set = set()
        for op in self.ops:
            target = (root / op.path).resolve()
            try:
                target.relative_to(root)
            except ValueError:
                raise ChangeSetError(
                    f"Path {op.path!r} escapes project root {root}"
                )
            if op.path in seen:
                raise ChangeSetError(f"Duplicate path in change set: {op.path!r}")
            seen.add(op.path)
            if op.kind == "write" and op.content is not None and len(op.content) > 2_000_000:
                raise ChangeSetError(
                    f"Write content for {op.path!r} exceeds 2MB cap"
                )

    # ------------------------------------------------------------------ #
    # Dry run — returns what the tree would look like post-apply
    # ------------------------------------------------------------------ #
    def dry_apply(self, project_root: str) -> Dict[str, str]:
        """
        Return {path: hypothetical_content} for every file the set touches.
        Used by the contract validator to check the *intended* post-apply
        state without writing anything to disk.
        """
        root = Path(project_root).resolve()
        result: Dict[str, str] = {}
        for op in self.ops:
            if op.kind == "write":
                result[op.path] = op.content or ""
            else:  # delete
                result[op.path] = ""
        return result

    # ------------------------------------------------------------------ #
    # Apply
    # ------------------------------------------------------------------ #
    def apply(self, project_root: str) -> ApplyReport:
        """Apply all ops. On any failure, rollback previously applied ops."""
        t0 = time.perf_counter()
        report = ApplyReport()

        try:
            self.validate(project_root)
        except ChangeSetError as exc:
            report.error = f"Pre-flight failed: {exc}"
            report.elapsed_ms = (time.perf_counter() - t0) * 1000.0
            return report

        if not self.ops:
            report.ok = True
            report.elapsed_ms = (time.perf_counter() - t0) * 1000.0
            return report

        root = Path(project_root).resolve()
        staging_dir = root / ".differix" / "staging" / uuid.uuid4().hex
        staging_dir.mkdir(parents=True, exist_ok=True)

        # back up existing content for every target path
        backups: Dict[str, Optional[str]] = {}
        try:
            for op in self.ops:
                target = root / op.path
                if target.exists() and target.is_file():
                    try:
                        backups[op.path] = target.read_text(encoding="utf-8")
                    except Exception as exc:
                        raise ChangeSetError(
                            f"Cannot read existing {op.path!r} for backup: {exc}"
                        )
                else:
                    backups[op.path] = None
        except ChangeSetError as exc:
            report.error = str(exc)
            _safe_rmtree(staging_dir)
            report.elapsed_ms = (time.perf_counter() - t0) * 1000.0
            return report

        # Phase 1.2: idempotency — skip writes that match existing content
        ops_to_apply: List[ChangeOp] = []
        for op in self.ops:
            if op.kind == "write":
                existing = backups.get(op.path)
                if existing is not None and existing == op.content:
                    report.skipped.append(op.path)
                    continue
            ops_to_apply.append(op)

        if not ops_to_apply:
            # All ops were idempotent no-ops; nothing to write
            _safe_rmtree(staging_dir)
            report.ok = True
            report.elapsed_ms = (time.perf_counter() - t0) * 1000.0
            return report

        # stage all new content to disk first
        staged_paths: Dict[str, Path] = {}
        try:
            for op in ops_to_apply:
                if op.kind != "write":
                    continue
                stage_target = staging_dir / op.path
                stage_target.parent.mkdir(parents=True, exist_ok=True)
                stage_target.write_text(op.content or "", encoding="utf-8")
                staged_paths[op.path] = stage_target
        except Exception as exc:
            report.error = f"Staging failed: {exc}"
            _safe_rmtree(staging_dir)
            report.elapsed_ms = (time.perf_counter() - t0) * 1000.0
            return report

        # now do the renames in rapid succession
        applied: List[str] = []
        try:
            for op in ops_to_apply:
                target = root / op.path
                if op.kind == "write":
                    target.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(staged_paths[op.path], target)
                    applied.append(op.path)
                else:  # delete
                    try:
                        target.unlink()
                    except FileNotFoundError:
                        pass
                    applied.append(op.path)
        except Exception as exc:
            # Roll back every file we've already touched
            rolled_back: List[str] = []
            for path in applied:
                backup = backups.get(path)
                try:
                    target = root / path
                    if backup is None:
                        try:
                            target.unlink()
                        except FileNotFoundError:
                            pass
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_text(backup, encoding="utf-8")
                    rolled_back.append(path)
                except Exception as rb_exc:
                    logger.error("Rollback failed for %s: %s", path, rb_exc)

            report.ok = False
            report.applied = applied
            report.rolled_back = rolled_back
            report.error = f"Apply failed after {len(applied)} ops: {exc}"
            _safe_rmtree(staging_dir)
            report.elapsed_ms = (time.perf_counter() - t0) * 1000.0
            return report

        _safe_rmtree(staging_dir)
        report.ok = True
        report.applied = applied
        report.elapsed_ms = (time.perf_counter() - t0) * 1000.0
        return report


def _safe_rmtree(path: Path) -> None:
    try:
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass


__all__ = ["ChangeOp", "ChangeSet", "ChangeSetError", "ApplyReport"]