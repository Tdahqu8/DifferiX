"""
tools/changelog_engine.py
--------------------------
Project-scoped iteration log manager for DifferiX.

Logs live at `<project_root>/iteration_logs/`, not at the process cwd.
This removes the class of bug where a `differix` process launched from one
directory writes logs into a different project's tree.

Public API:
  - ChangelogEngine(project_root=".", session_id=None)
  - engine.generate_diff(old_text, new_text, filename) -> dict
  - engine.record_iteration(state, diff_data="") -> None

Disk layout (per project):
  <project_root>/iteration_logs/session_YYYY-MM-DD_HH-MM-SS.json
  <project_root>/iteration_logs/session_YYYY-MM-DD_HH-MM-SS_SUMMARY.md
  <project_root>/iteration_logs/archived_logs.zip   (rotated, capped)
"""

import difflib
import json
import os
import tempfile
import zipfile
from datetime import datetime

ARCHIVE_NAME = "archived_logs.zip"
LOGS_SUBDIR = "iteration_logs"
DEFAULT_MAX_RECENT = 10


class ChangelogEngine:
    def __init__(self, project_root: str = ".", session_id: str = None):
        self.project_root = os.path.abspath(project_root)
        self.logs_dir = os.path.join(self.project_root, LOGS_SUBDIR)

        os.makedirs(self.logs_dir, exist_ok=True)

        if not session_id:
            session_id = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self.session_id = session_id
        self.log_filepath = os.path.join(
            self.logs_dir, f"session_{session_id}.json"
        )
        self.md_filepath = os.path.join(
            self.logs_dir, f"session_{session_id}_SUMMARY.md"
        )

        # 1. Rotate/archive old logs first (never touches the current session)
        self._clean_and_archive_logs()

        # 2. Create the current session log file
        self._init_log_file()

    # ------------------------------------------------------------------ #
    # Log rotation
    # ------------------------------------------------------------------ #
    def _clean_and_archive_logs(self, max_recent: int = DEFAULT_MAX_RECENT) -> None:
        """
        - Delete empty log files (no iterations recorded).
        - Keep the most recent `max_recent` non-empty logs.
        - Zip and remove older non-empty logs (and their summary MDs).
        """
        if not os.path.isdir(self.logs_dir):
            return

        valid_logs = []
        archive_path = os.path.join(self.logs_dir, ARCHIVE_NAME)
        current_log_abs = os.path.abspath(self.log_filepath)

        for fname in sorted(os.listdir(self.logs_dir)):
            if not fname.endswith(".json") or fname == ARCHIVE_NAME:
                continue

            fpath = os.path.join(self.logs_dir, fname)

            if os.path.abspath(fpath) == current_log_abs:
                continue

            is_empty = True
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict) and data.get("iterations"):
                    is_empty = False
                elif isinstance(data, list) and len(data) > 0:
                    is_empty = False
            except Exception:
                is_empty = True

            md_path = fpath.replace(".json", "_SUMMARY.md")

            if is_empty:
                for p in (fpath, md_path):
                    try:
                        os.remove(p)
                    except OSError:
                        pass
            else:
                try:
                    mtime = os.path.getmtime(fpath)
                except OSError:
                    continue
                valid_logs.append((fpath, mtime))

        # Newest first
        valid_logs.sort(key=lambda x: x[1], reverse=True)
        old_logs = valid_logs[max_recent:]

        if not old_logs:
            return

        try:
            with zipfile.ZipFile(
                archive_path, "a", compression=zipfile.ZIP_DEFLATED
            ) as zipf:
                for fpath, _ in old_logs:
                    if os.path.exists(fpath):
                        zipf.write(fpath, arcname=os.path.basename(fpath))
                        try:
                            os.remove(fpath)
                        except OSError:
                            pass

                    md_path = fpath.replace(".json", "_SUMMARY.md")
                    if os.path.exists(md_path):
                        zipf.write(md_path, arcname=os.path.basename(md_path))
                        try:
                            os.remove(md_path)
                        except OSError:
                            pass
        except Exception as exc:
            print(f"[ChangelogEngine] Archive rotation failed: {exc}")

    # ------------------------------------------------------------------ #
    # Session init
    # ------------------------------------------------------------------ #
    def _init_log_file(self) -> None:
        if os.path.exists(self.log_filepath):
            return
        initial_data = {
            "session_id": self.session_id,
            "project_root": self.project_root,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "iterations": [],
        }
        _atomic_write_json(self.log_filepath, initial_data)

    # ------------------------------------------------------------------ #
    # Diff helper
    # ------------------------------------------------------------------ #
    def generate_diff(self, old_text: str, new_text: str, filename: str) -> dict:
        old_lines = old_text.splitlines(keepends=True)
        new_lines = new_text.splitlines(keepends=True)

        diff = list(
            difflib.unified_diff(
                old_lines, new_lines, fromfile=filename, tofile=filename
            )
        )

        added = sum(
            1 for line in diff
            if line.startswith("+") and not line.startswith("+++")
        )
        removed = sum(
            1 for line in diff
            if line.startswith("-") and not line.startswith("---")
        )

        clean_diff = "".join(diff[:40])

        return {
            "file": filename,
            "lines_added": added,
            "lines_removed": removed,
            "unified_diff": (
                clean_diff if clean_diff
                else "File created from zero/no diffs"
            ),
        }

    # ------------------------------------------------------------------ #
    # Iteration recording
    # ------------------------------------------------------------------ #
    def record_iteration(self, state: dict, diff_data="") -> None:
        os.makedirs(self.logs_dir, exist_ok=True)

        # Load existing
        data = {}
        if os.path.exists(self.log_filepath):
            try:
                with open(self.log_filepath, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    data = loaded
                elif isinstance(loaded, list):
                    data = {"iterations": loaded}
            except (json.JSONDecodeError, FileNotFoundError, OSError):
                data = {}

        if not isinstance(data.get("iterations"), list):
            data["iterations"] = []

        # Defensive: generated_code can be a dict OR a str (editor full-rewrite mode)
        gen = state.get("generated_code") if isinstance(state, dict) else None
        if isinstance(gen, dict):
            target_file = gen.get("target_file")
        else:
            target_file = None
        if not target_file:
            target_file = (
                (state.get("active_target_file") if isinstance(state, dict) else None)
                or (state.get("target_file") if isinstance(state, dict) else None)
            )

        if isinstance(gen, dict):
            generated_snapshot = {
                "target_file": gen.get("target_file"),
                "apply_result": gen.get("apply_result"),
            }
        elif isinstance(gen, str):
            generated_snapshot = {"kind": "str", "length": len(gen)}
        else:
            generated_snapshot = None

        new_entry = {
            "iteration": (state.get("iteration_count", 0) if isinstance(state, dict) else 0),
            "step": (state.get("current_step") if isinstance(state, dict) else None),
            "target_file": target_file,
            "generated_code_summary": generated_snapshot,
            "review": (state.get("review_result") if isinstance(state, dict) else None),
            "diff": diff_data,
        }
        data["iterations"].append(new_entry)

        _atomic_write_json(self.log_filepath, data)


# ---------------------------------------------------------------------------
# Atomic write helper
# ---------------------------------------------------------------------------
def _atomic_write_json(path: str, data) -> None:
    """Temp + os.replace. Readers never see a partial file."""
    target_dir = os.path.dirname(os.path.abspath(path))
    os.makedirs(target_dir, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target_dir, suffix=".json.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


__all__ = ["ChangelogEngine"]