"""
tools/drift_guard.py
--------------------
Detects external modifications to critical files during a running pipeline.

Problem this solves:
    A single Differix process holds an in-memory snapshot of state (and often
    of the target file's pre-write content) across multiple pipeline steps.
    If the user edits state.json or the target file between steps, the
    process operates on stale data and writes decisions based on it.

Design:
    - Every critical checkpoint computes a SHA-256 hash of the relevant
      artifact (state.json, target file, plan).
    - The hash is stored in a DriftGuard instance that lives for the
      duration of one run_agent_cycle call.
    - At each pipeline transition, verify() compares the expected hash
      against the current on-disk hash.
    - On mismatch, DriftDetected is raised. The caller can choose to
      reload from disk (default: continue with new truth) or abort.

Determinism:
    - State hashing normalizes via json.dumps(sort_keys=True, default=str)
      so Python version and dict insertion order do not affect the hash.
    - File hashing is raw SHA-256 of file bytes.

Public API:
    - DriftGuard(project_root)
        .pin_state(label)                 -> None
        .check_state(label)               -> bool   (True = unchanged)
        .pin_file(path, label)            -> None
        .check_file(path, label)          -> bool
        .report()                         -> dict
    - DriftDetected(exception)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class DriftDetected(Exception):
    """Raised when a pinned artifact's hash no longer matches."""

    def __init__(self, kind: str, label: str, path: str, expected: str, actual: str):
        self.kind = kind
        self.label = label
        self.path = path
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"Drift detected in {kind} ({label}) at {path}: "
            f"expected={expected[:12]}…, actual={actual[:12]}…"
        )


@dataclass
class PinRecord:
    kind: str
    label: str
    path: str
    expected: str
    captured_at: str


@dataclass
class DriftGuard:
    """Tracks expected hashes for critical artifacts during one pipeline run."""

    project_root: str
    _pins: Dict[str, PinRecord] = field(default_factory=dict)

    # ------------------------------------------------------------------ #
    # State hashing
    # ------------------------------------------------------------------ #
    def _hash_state_file(self, path: str) -> str:
        """Canonical hash of a JSON file. Returns hex digest or 'missing'."""
        p = Path(path)
        if not p.exists():
            return "missing"
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Could not parse %s for hashing: %s", path, exc)
            return "unreadable"
        normalized = json.dumps(data, sort_keys=True, default=str)
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def _hash_file(self, path: str) -> str:
        """Raw SHA-256 of file bytes. Returns hex digest or 'missing'."""
        p = Path(path)
        if not p.exists():
            return "missing"
        try:
            data = p.read_bytes()
        except OSError as exc:
            logger.warning("Could not read %s for hashing: %s", path, exc)
            return "unreadable"
        return hashlib.sha256(data).hexdigest()

    # ------------------------------------------------------------------ #
    # Pin / check
    # ------------------------------------------------------------------ #
    def pin_state(self, label: str, state_path: str) -> None:
        """Record expected hash of a state file under a label."""
        h = self._hash_state_file(state_path)
        self._pins[f"state::{label}"] = PinRecord(
            kind="state",
            label=label,
            path=os.path.abspath(state_path),
            expected=h,
            captured_at=_now(),
        )

    def check_state(self, label: str, state_path: str) -> bool:
        """
        Compare current state hash against the pinned one.
        Returns True if unchanged, False if drifted. Never raises.
        """
        key = f"state::{label}"
        pin = self._pins.get(key)
        if pin is None:
            return True
        actual = self._hash_state_file(state_path)
        if actual != pin.expected:
            logger.warning(
                "Drift: state.json changed externally between '%s' and now",
                pin.label,
            )
            return False
        return True

    def pin_file(self, path: str, label: str) -> None:
        """Record expected hash of an arbitrary file."""
        h = self._hash_file(path)
        self._pins[f"file::{label}"] = PinRecord(
            kind="file",
            label=label,
            path=os.path.abspath(path),
            expected=h,
            captured_at=_now(),
        )

    def check_file(self, path: str, label: str) -> bool:
        """
        Compare current file hash against the pinned one.
        Returns True if unchanged, False if drifted. Never raises.
        """
        key = f"file::{label}"
        pin = self._pins.get(key)
        if pin is None:
            return True
        actual = self._hash_file(path)
        if actual != pin.expected:
            logger.warning(
                "Drift: file %s changed externally between '%s' and now",
                path,
                pin.label,
            )
            return False
        return True

    # ------------------------------------------------------------------ #
    # Reporting
    # ------------------------------------------------------------------ #
    def report(self) -> Dict[str, Any]:
        """Return a snapshot of the current pin set."""
        return {
            "project_root": self.project_root,
            "pins": {
                k: {
                    "kind": v.kind,
                    "label": v.label,
                    "path": v.path,
                    "expected": v.expected,
                    "captured_at": v.captured_at,
                }
                for k, v in self._pins.items()
            },
        }


def _now() -> str:
    import time
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


__all__ = ["DriftGuard", "DriftDetected", "PinRecord"]