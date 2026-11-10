"""
tools/session_adr.py
---------------------
Compressed session history (Architecture Decision Records) for VegeX.

Storage layout:
  .vegex/session_adr.jsonl         append-only, one JSON object per line
  .vegex/session_adr.compact.json  derived, written periodically

Raw JSONL is the source of truth. Compact JSON is regenerated from the raw
when the raw file exceeds RAW_COMPACT_THRESHOLD lines, or on explicit call.
Deleting the compact file is safe — it will be rebuilt on next read.

Public API:
  - ADREntry (dataclass)
  - SessionADR(root).append(entry)
  - SessionADR(root).get_recent_summary(limit=5, token_budget=300) -> str
  - SessionADR(root).get_recent_entries(limit=20) -> List[ADREntry]
  - SessionADR(root).compact(keep_raw=200)
  - SessionADR(root).stats()
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

# Constants
RAW_COMPACT_THRESHOLD = 400   # compact when JSONL exceeds this many lines
DEFAULT_KEEP_RAW = 200
SCHEMA_VERSION = "1.0.0"

# Rough token estimator. Deliberately conservative (4 chars/token) with an
# additional 10% safety margin applied at format time. Real tokenizers vary
# by ±20% across providers — this is for budgeting, not billing.
_CHARS_PER_TOKEN = 4
_BUDGET_SAFETY_MARGIN = 0.9


# ---------------------------------------------------------------------------
# ADR entry
# ---------------------------------------------------------------------------
@dataclass
class ADREntry:
    session_id: str
    cycle: int
    timestamp: str  # ISO-8601 UTC
    action: str     # "research" | "edit" | "review" | "refactor" | "bugfix" | "promote"
    outcome: str    # "accepted" | "rejected" | "reverted" | "info"
    files_touched: List[str] = field(default_factory=list)
    contract_changes: List[str] = field(default_factory=list)
    decisions: List[str] = field(default_factory=list)
    duration_ms: int = 0
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_json_line(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ADREntry":
        return cls(
            session_id=str(d.get("session_id", "")),
            cycle=int(d.get("cycle", 0)),
            timestamp=str(d.get("timestamp", "")),
            action=str(d.get("action", "unknown")),
            outcome=str(d.get("outcome", "info")),
            files_touched=list(d.get("files_touched", []) or []),
            contract_changes=list(d.get("contract_changes", []) or []),
            decisions=list(d.get("decisions", []) or []),
            duration_ms=int(d.get("duration_ms", 0) or 0),
            extra=dict(d.get("extra", {}) or {}),
        )


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------
class SessionADR:
    def __init__(self, project_root: str) -> None:
        self.root = Path(project_root).resolve()
        self.dir = self.root / ".vegex"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.raw_path = self.dir / "session_adr.jsonl"
        self.compact_path = self.dir / "session_adr.compact.json"

    # ------------------------------------------------------------------ #
    # Append
    # ------------------------------------------------------------------ #
    def append(self, entry: ADREntry) -> None:
        """Append one entry. Uses O_APPEND for atomicity on POSIX."""
        line = entry.to_json_line() + "\n"
        try:
            fd = os.open(self.raw_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
            try:
                os.write(fd, line.encode("utf-8"))
            finally:
                os.close(fd)
        except Exception as exc:
            logger.warning("ADR append failed: %s", exc)
            return

        # Opportunistic compaction
        try:
            if self._line_count() >= RAW_COMPACT_THRESHOLD:
                self.compact()
        except Exception as exc:
            logger.warning("ADR compact check failed: %s", exc)

    def _line_count(self) -> int:
        try:
            with open(self.raw_path, "rb") as f:
                return sum(1 for _ in f)
        except FileNotFoundError:
            return 0

    # ------------------------------------------------------------------ #
    # Read
    # ------------------------------------------------------------------ #
    def get_recent_entries(self, limit: int = 20) -> List[ADREntry]:
        """Read the tail of the raw JSONL. Corrupt lines are skipped."""
        if not self.raw_path.exists():
            return []
        try:
            with open(self.raw_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except Exception as exc:
            logger.warning("ADR read failed: %s", exc)
            return []

        entries: List[ADREntry] = []
        for line in lines[-limit:]:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(ADREntry.from_dict(json.loads(line)))
            except Exception:
                continue
        return entries

    def get_recent_summary(
        self,
        limit: int = 5,
        token_budget: int = 300,
    ) -> str:
        """
        Render the last `limit` entries as a compact LLM-injectable string.

        Returns empty string if there are no entries.
        """
        entries = self.get_recent_entries(limit=limit)
        if not entries:
            return ""

        char_budget = int(token_budget * _CHARS_PER_TOKEN * _BUDGET_SAFETY_MARGIN)

        # Format newest-first, drop entries when we exceed budget
        rendered: List[str] = ["## RECENT SESSION DECISIONS", ""]
        for e in reversed(entries):
            block = self._format_entry(e)
            if sum(len(l) + 1 for l in rendered) + len(block) > char_budget:
                break
            rendered.append(block)

        if len(rendered) <= 2:
            return ""  # nothing fit
        return "\n".join(rendered)

    @staticmethod
    def _format_entry(e: ADREntry) -> str:
        ts_short = e.timestamp[:16].replace("T", " ") if e.timestamp else "?"
        lines = [f"[cycle {e.cycle} | {ts_short}] {e.action} → {e.outcome}"]
        if e.files_touched:
            lines.append(f"  files: {', '.join(e.files_touched[:4])}")
        if e.contract_changes:
            lines.append(f"  contract: {'; '.join(e.contract_changes[:3])}")
        if e.decisions:
            for d in e.decisions[:2]:
                short = d if len(d) <= 140 else d[:137] + "..."
                lines.append(f"  - {short}")
        return "\n".join(lines) + "\n"

    # ------------------------------------------------------------------ #
    # Compaction
    # ------------------------------------------------------------------ #
    def compact(self, keep_raw: int = DEFAULT_KEEP_RAW) -> Dict[str, Any]:
        """
        Prune the raw JSONL to the last `keep_raw` entries, and write a
        compact summary of every entry ever seen. Idempotent.
        """
        if not self.raw_path.exists():
            return {"status": "no_raw"}

        try:
            with open(self.raw_path, "r", encoding="utf-8") as f:
                all_lines = [l for l in f if l.strip()]
        except Exception as exc:
            return {"status": "read_error", "error": str(exc)}

        total = len(all_lines)
        kept_lines = all_lines[-keep_raw:] if total > keep_raw else all_lines

        # All entries (for compact json)
        all_entries: List[ADREntry] = []
        for line in all_lines:
            try:
                all_entries.append(ADREntry.from_dict(json.loads(line)))
            except Exception:
                continue

        # Pruned raw
        try:
            fd, tmp = tempfile.mkstemp(dir=str(self.dir), suffix=".jsonl.tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.writelines(kept_lines)
                os.replace(tmp, self.raw_path)
            except Exception:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
        except Exception as exc:
            return {"status": "write_raw_error", "error": str(exc)}

        # Compact file
        compact_data = {
            "schema_version": SCHEMA_VERSION,
            "compacted_at": _utc_iso(),
            "total_entries_ever": total,
            "kept_in_raw": len(kept_lines),
            "entries": [asdict(e) for e in all_entries],
        }
        try:
            fd, tmp = tempfile.mkstemp(dir=str(self.dir), suffix=".json.tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(compact_data, f, ensure_ascii=False, indent=2)
                os.replace(tmp, self.compact_path)
            except Exception:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
        except Exception as exc:
            return {"status": "write_compact_error", "error": str(exc)}

        return {
            "status": "ok",
            "total_entries": total,
            "kept_in_raw": len(kept_lines),
            "pruned": total - len(kept_lines),
        }

    # ------------------------------------------------------------------ #
    # Stats / lifecycle
    # ------------------------------------------------------------------ #
    def stats(self) -> Dict[str, Any]:
        return {
            "raw_path": str(self.raw_path),
            "raw_exists": self.raw_path.exists(),
            "raw_lines": self._line_count(),
            "compact_exists": self.compact_path.exists(),
            "compact_threshold": RAW_COMPACT_THRESHOLD,
        }


# ---------------------------------------------------------------------------
# Entry builder (convenience)
# ---------------------------------------------------------------------------
def build_entry(
    session_id: str,
    cycle: int,
    action: str,
    outcome: str,
    files_touched: Optional[Iterable[str]] = None,
    contract_changes: Optional[Iterable[str]] = None,
    decisions: Optional[Iterable[str]] = None,
    duration_ms: int = 0,
    extra: Optional[Dict[str, Any]] = None,
) -> ADREntry:
    return ADREntry(
        session_id=session_id or str(uuid.uuid4()),
        cycle=int(cycle),
        timestamp=_utc_iso(),
        action=action,
        outcome=outcome,
        files_touched=list(files_touched or []),
        contract_changes=list(contract_changes or []),
        decisions=list(decisions or []),
        duration_ms=int(duration_ms),
        extra=dict(extra or {}),
    )


def _utc_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


__all__ = ["ADREntry", "SessionADR", "build_entry"]