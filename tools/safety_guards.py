"""
Phase 3: Deep Defense-in-Phase Safety Guardrails.

Provides zero-trust pre-flight validation gates before disk writes:
- Standardized T5_FEEDBACK structured payloads
- Pre-flight AST and py_compile / node syntax compilation gate
- Cryptographic file hashing and context freshness concurrency guard
- AST-based file, function, and class bloat/monolith prevention limits
"""

from __future__ import annotations

import ast
import hashlib
import os
import py_compile
import shutil
import subprocess
import tempfile
from typing import Any, Dict, List, Optional


class FreshnessViolation(PermissionError):
    """Raised when a target file changed on disk since its last read."""

    def __init__(
        self,
        message: str,
        feedback: Optional[Dict[str, Any]] = None,
        file_path: str = "",
        expected_hash: Optional[str] = None,
        actual_hash: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.file_path = file_path
        self.expected_hash = expected_hash
        self.actual_hash = actual_hash
        self.feedback = feedback or build_t5_feedback(
            ok=False,
            mode="context_freshness",
            failed=[
                {
                    "reason": message,
                    "file_path": file_path,
                    "expected_hash": expected_hash,
                    "actual_hash": actual_hash,
                    "line": 1,
                    "symbol": os.path.basename(file_path) or "<file>",
                }
            ],
        )


def build_t5_feedback(
    ok: bool,
    mode: str,
    matched: Optional[List[Dict[str, Any]]] = None,
    failed: Optional[List[Dict[str, Any]]] = None,
    **extra: Any,
) -> Dict[str, Any]:
    """Build a standardized T5_FEEDBACK structured dictionary."""
    payload: Dict[str, Any] = {
        "ok": bool(ok),
        "mode": mode,
        "matched": list(matched) if matched else [],
        "failed": list(failed) if failed else [],
    }
    if extra:
        payload.update(extra)
    return payload


def _validate_python_syntax(content: str, file_path: str) -> Dict[str, Any]:
    """Run AST parse and isolated py_compile validation on Python source."""
    try:
        ast.parse(content, filename=file_path)
    except SyntaxError as exc:
        return build_t5_feedback(
            ok=False,
            mode="preflight_syntax_gate",
            failed=[
                {
                    "reason": f"AST SyntaxError: {exc.msg}",
                    "line": exc.lineno or 1,
                    "offset": exc.offset or 0,
                    "symbol": os.path.basename(file_path) or "<module>",
                }
            ],
        )

    with tempfile.TemporaryDirectory(prefix="differix_pycompile_") as tmp_dir:
        tmp_file = os.path.join(tmp_dir, os.path.basename(file_path) or "candidate.py")
        if not tmp_file.endswith(".py"):
            tmp_file = f"{tmp_file}.py"
        with open(tmp_file, "w", encoding="utf-8") as handle:
            handle.write(content)
        try:
            py_compile.compile(tmp_file, doraise=True)
        except py_compile.PyCompileError as exc:
            lineno = getattr(exc.exc_value, "lineno", 1) or 1
            offset = getattr(exc.exc_value, "offset", 0) or 0
            return build_t5_feedback(
                ok=False,
                mode="preflight_syntax_gate",
                failed=[
                    {
                        "reason": f"py_compile failed: {exc.msg}",
                        "line": lineno,
                        "offset": offset,
                        "symbol": os.path.basename(file_path) or "<module>",
                    }
                ],
            )

    return build_t5_feedback(
        ok=True,
        mode="preflight_syntax_gate",
        matched=[{"file_path": file_path, "validator": "ast+py_compile"}],
    )


def _validate_js_syntax(content: str, file_path: str) -> Dict[str, Any]:
    """Run `node --check` syntax validation for JS/TS files when node is available."""
    node_bin = shutil.which("node")
    if not node_bin:
        return build_t5_feedback(
            ok=True,
            mode="preflight_syntax_gate",
            matched=[{"file_path": file_path, "validator": "skipped_no_node"}],
        )

    ext = os.path.splitext(file_path)[1] or ".js"
    with tempfile.TemporaryDirectory(prefix="differix_nodecheck_") as tmp_dir:
        tmp_file = os.path.join(tmp_dir, f"candidate{ext}")
        with open(tmp_file, "w", encoding="utf-8") as handle:
            handle.write(content)
        proc = subprocess.run(
            [node_bin, "--check", tmp_file],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if proc.returncode != 0:
            err_msg = (proc.stderr or proc.stdout or "node --check failed").strip()
            return build_t5_feedback(
                ok=False,
                mode="preflight_syntax_gate",
                failed=[
                    {
                        "reason": f"node --check failed: {err_msg}",
                        "line": 1,
                        "offset": 0,
                        "symbol": os.path.basename(file_path) or "<script>",
                    }
                ],
            )

    return build_t5_feedback(
        ok=True,
        mode="preflight_syntax_gate",
        matched=[{"file_path": file_path, "validator": "node --check"}],
    )


def preflight_syntax_gate(content: str, file_path: str = "<string>.py") -> Dict[str, Any]:
    """Validate candidate code syntax prior to writing to disk."""
    ext = os.path.splitext(file_path)[1].lower()
    if ext in ("", ".py", ".pyw"):
        return _validate_python_syntax(content, file_path)
    if ext in (".js", ".mjs", ".cjs"):
        return _validate_js_syntax(content, file_path)
    return build_t5_feedback(
        ok=True,
        mode="preflight_syntax_gate",
        matched=[{"file_path": file_path, "validator": "passthrough"}],
    )


def compute_content_hash(content: str, algorithm: str = "sha256") -> str:
    """Compute a cryptographic hex digest for in-memory text content."""
    hasher = hashlib.new(algorithm)
    hasher.update(content.encode("utf-8"))
    return hasher.hexdigest()


def compute_file_hash(file_path: str, algorithm: str = "sha256") -> Optional[str]:
    """Compute a cryptographic hex digest of a file on disk, or None if missing."""
    hasher = hashlib.new(algorithm)
    if not file_path or not os.path.isfile(file_path):
        return None
    with open(file_path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def verify_context_freshness_guard(
    file_path: str,
    expected_hash: Optional[str] = None,
    state: Optional[Dict[str, Any]] = None,
    algorithm: str = "sha256",
    raise_on_mismatch: bool = True,
) -> Dict[str, Any]:
    """Verify target file hash matches the expected hash recorded at read time."""
    expected = expected_hash
    if expected is None and state is not None:
        expected = state.get("_expected_file_hash")

    actual_hash = compute_file_hash(file_path, algorithm=algorithm)

    if expected is None:
        if actual_hash is None:
            return build_t5_feedback(
                ok=True,
                mode="context_freshness",
                matched=[{"file_path": file_path, "hash": None, "status": "new_file"}],
            )
        msg = f"Missing expected file hash for existing file '{file_path}'."
        feedback = build_t5_feedback(
            ok=False,
            mode="context_freshness",
            failed=[
                {
                    "reason": msg,
                    "file_path": file_path,
                    "expected_hash": None,
                    "actual_hash": actual_hash,
                    "line": 1,
                    "symbol": os.path.basename(file_path),
                }
            ],
        )
        if raise_on_mismatch:
            raise FreshnessViolation(
                msg,
                feedback=feedback,
                file_path=file_path,
                expected_hash=None,
                actual_hash=actual_hash,
            )
        return feedback

    if actual_hash != expected:
        msg = (
            f"Context freshness violation on '{file_path}': "
            f"expected hash {expected}, found {actual_hash}."
        )
        feedback = build_t5_feedback(
            ok=False,
            mode="context_freshness",
            failed=[
                {
                    "reason": msg,
                    "file_path": file_path,
                    "expected_hash": expected,
                    "actual_hash": actual_hash,
                    "line": 1,
                    "symbol": os.path.basename(file_path),
                }
            ],
        )
        if raise_on_mismatch:
            raise FreshnessViolation(
                msg,
                feedback=feedback,
                file_path=file_path,
                expected_hash=expected,
                actual_hash=actual_hash,
            )
        return feedback

    return build_t5_feedback(
        ok=True,
        mode="context_freshness",
        matched=[{"file_path": file_path, "hash": actual_hash, "status": "fresh"}],
    )


def validate_size_limits_guard(
    content: str,
    max_lines: int = 300,
    max_func_lines: int = 100,
    file_path: str = "<string>.py",
) -> Dict[str, Any]:
    """Enforce file, function, and class line limits via AST inspection."""
    lines = content.splitlines()
    total_lines = len(lines)
    matched: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []

    if total_lines > max_lines:
        failed.append(
            {
                "reason": (
                    f"File exceeds {max_lines}-line limit ({total_lines} lines). "
                    "Refactor into modular sub-files."
                ),
                "line": total_lines,
                "limit": max_lines,
                "actual": total_lines,
                "symbol": os.path.basename(file_path) or "<module>",
            }
        )
    else:
        matched.append(
            {
                "symbol": os.path.basename(file_path) or "<module>",
                "lines": total_lines,
                "limit": max_lines,
            }
        )

    ext = os.path.splitext(file_path)[1].lower()
    if ext in ("", ".py", ".pyw"):
        try:
            tree = ast.parse(content, filename=file_path)
        except SyntaxError as exc:
            failed.append(
                {
                    "reason": f"AST SyntaxError during size inspection: {exc.msg}",
                    "line": exc.lineno or 1,
                    "offset": exc.offset or 0,
                    "symbol": os.path.basename(file_path) or "<module>",
                }
            )
            return build_t5_feedback(
                ok=False,
                mode="size_limits",
                matched=matched,
                failed=failed,
                total_lines=total_lines,
            )

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                end_line = getattr(node, "end_lineno", node.lineno) or node.lineno
                body_lines = end_line - node.lineno + 1
                kind = "class" if isinstance(node, ast.ClassDef) else "function"
                if body_lines > max_func_lines:
                    failed.append(
                        {
                            "reason": (
                                f"{kind} '{node.name}' exceeds {max_func_lines}-line limit "
                                f"({body_lines} lines). Split into smaller helpers."
                            ),
                            "line": node.lineno,
                            "end_line": end_line,
                            "limit": max_func_lines,
                            "actual": body_lines,
                            "symbol": node.name,
                            "kind": kind,
                        }
                    )
                else:
                    matched.append(
                        {
                            "symbol": node.name,
                            "kind": kind,
                            "line": node.lineno,
                            "lines": body_lines,
                            "limit": max_func_lines,
                        }
                    )

    return build_t5_feedback(
        ok=len(failed) == 0,
        mode="size_limits",
        matched=matched,
        failed=failed,
        total_lines=total_lines,
    )