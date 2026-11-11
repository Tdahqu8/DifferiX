"""
tools/project_structure.py
--------------------------
Human-readable project tree generator for DifferiX.

Produces `PROJECT_STRUCTURE.md` at the project root and a small metadata
sidecar at `.differix/tree_meta.json` (gitignored).

Change detection uses a SHA-256 content hash over every tracked file — NOT
the mtime+size pair used by the older implementation, which silently
missed same-second edits and same-size replacements.

Public API:
  - generate_or_update_structure(project_path) -> dict
      {
        "updated": bool,
        "fingerprint": str,       # sha256 over (relpath + content-sha256)
        "file_count": int,
        "structure_text": str,
      }
  - get_structure_text(project_path) -> str
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
_DIFFERIX_DIR = ".differix"
_TREE_META_FILE = "tree_meta.json"
_TREE_MD_FILE = "PROJECT_STRUCTURE.md"

_IGNORE_DIRS = {
    ".git", ".differix", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".tox", ".venv", "venv", "env", "node_modules",
    "bin", "obj", "build", "dist", ".idea", ".vscode",
    "iteration_logs", "AI_Orchestrator_Backups",
}

_IGNORE_FILES = {
    ".env",
    "PROJECT_STRUCTURE.md",
    "structure_meta.json",           # deprecated sidecar from prior versions
    "projects_registry.json",
    "skills_config.json",
    "active_models.json",
    "models_cache.json",
    ".agent_bindings.json",
    ".openrouter_models_cache.json",
}

_IGNORE_SUFFIXES = (
    ".pyc", ".pyo", ".pyd", ".so", ".dll", ".dylib",
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp",
    ".zip", ".tar", ".gz", ".7z", ".rar",
    ".mp4", ".webm", ".mov", ".mp3", ".wav",
)


# ---------------------------------------------------------------------------
# File walk
# ---------------------------------------------------------------------------
def _iter_tracked_files(root: Path) -> List[Path]:
    """Deterministic walk: sorted dirs, sorted files, filters applied."""
    out: List[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in _IGNORE_DIRS and not d.startswith(".")
        )
        for fname in sorted(filenames):
            if fname in _IGNORE_FILES:
                continue
            if any(fname.endswith(s) for s in _IGNORE_SUFFIXES):
                continue
            out.append(Path(dirpath) / fname)
    return out


# ---------------------------------------------------------------------------
# Fingerprint
# ---------------------------------------------------------------------------
def _compute_fingerprint(root: Path, files: List[Path]) -> str:
    """
    Deterministic fingerprint:
      sha256 over sorted [relpath \\0 sha256(content) \\n] entries.
    """
    hasher = hashlib.sha256()
    for f in files:
        try:
            rel = f.relative_to(root).as_posix()
        except ValueError:
            continue
        hasher.update(rel.encode("utf-8"))
        hasher.update(b"\0")
        try:
            content = f.read_bytes()
            hasher.update(hashlib.sha256(content).digest())
        except OSError:
            hasher.update(b"<unreadable>")
        hasher.update(b"\n")
    return hasher.hexdigest()


# ---------------------------------------------------------------------------
# Tree rendering
# ---------------------------------------------------------------------------
def _build_tree_text(root: Path, files: List[Path]) -> str:
    tree: Dict[str, Any] = {}
    for f in files:
        try:
            rel = f.relative_to(root).as_posix()
        except ValueError:
            continue
        parts = rel.split("/")
        current = tree
        for part in parts:
            current = current.setdefault(part, {})

    def render(node: Dict[str, Any], prefix: str = "") -> List[str]:
        lines: List[str] = []
        keys = sorted(node.keys())
        for i, key in enumerate(keys):
            is_last = (i == len(keys) - 1)
            connector = "└── " if is_last else "├── "
            if node[key]:
                lines.append(f"{prefix}{connector}{key}/")
                new_prefix = prefix + ("    " if is_last else "│   ")
                lines.extend(render(node[key], new_prefix))
            else:
                lines.append(f"{prefix}{connector}{key}")
        return lines

    body = "\n".join(render(tree))
    generated = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    return (
        f"# Project Structure\n\n"
        f"Generated on: {generated}\n\n"
        f"```\n{body}\n```\n"
    )


# ---------------------------------------------------------------------------
# Atomic I/O
# ---------------------------------------------------------------------------
def _atomic_write(path: Path, content: str) -> None:
    """Write via temp file + os.replace so readers never see a partial file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _load_meta(meta_path: Path) -> Dict[str, Any]:
    if not meta_path.exists():
        return {}
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def generate_or_update_structure(project_path: str) -> Dict[str, Any]:
    """
    Walk the project, compute a content-hash fingerprint, and (re)write the
    human-readable tree if anything changed.

    Idempotent: two calls without source changes → second call reports
    updated=False and returns the identical structure_text.
    """
    root = Path(project_path).resolve()
    files = _iter_tracked_files(root)
    current_fp = _compute_fingerprint(root, files)

    veg_dir = root / _DIFFERIX_DIR
    meta_path = veg_dir / _TREE_META_FILE
    md_path = root / _TREE_MD_FILE

    meta = _load_meta(meta_path)
    old_fp = meta.get("fingerprint")

    structure_text = ""
    updated = False

    if current_fp != old_fp or not md_path.exists():
        structure_text = _build_tree_text(root, files)
        _atomic_write(md_path, structure_text)
        _atomic_write(
            meta_path,
            json.dumps(
                {
                    "schema_version": "2.0.0",
                    "fingerprint": current_fp,
                    "updated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
                    "file_count": len(files),
                },
                indent=2,
                sort_keys=True,
            ),
        )
        updated = True
    else:
        try:
            structure_text = md_path.read_text(encoding="utf-8")
        except OSError:
            structure_text = ""

    return {
        "updated": updated,
        "fingerprint": current_fp,
        "file_count": len(files),
        "structure_text": structure_text,
    }


def get_structure_text(project_path: str) -> str:
    """Convenience accessor — returns just the rendered MD text."""
    return generate_or_update_structure(project_path)["structure_text"]


__all__ = [
    "generate_or_update_structure",
    "get_structure_text",
]