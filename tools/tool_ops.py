"""
DifferiX Tool Operations - Core Refactoring Utilities

Provides idempotent file writing, py_compile pre-flight checks, fenced-code
extraction, search/replace block parsing, and file/function size validation.
All functions return structured dictionaries with explicit error handling.
"""

import ast
import os
import re
import subprocess
import sys
from typing import Any, Dict, List, Optional


def idempotent_write_file(
    file_path: str,
    content: str,
    encoding: str = "utf-8",
    create_dirs: bool = True,
) -> Dict[str, Any]:
    """Write content to file_path only when content differs from disk."""
    if not isinstance(file_path, str) or not file_path.strip():
        return {
            "success": False, "written": False, "skipped": False,
            "path": file_path, "bytes_written": 0,
            "error": "file_path must be a non-empty string",
        }
    if not isinstance(content, str):
        return {
            "success": False, "written": False, "skipped": False,
            "path": file_path, "bytes_written": 0,
            "error": "content must be a string",
        }
    try:
        if os.path.isdir(file_path):
            return {
                "success": False, "written": False, "skipped": False,
                "path": file_path, "bytes_written": 0,
                "error": f"Target path is a directory: {file_path}",
            }
        if os.path.exists(file_path):
            with open(file_path, "r", encoding=encoding, newline="") as fh:
                if fh.read() == content:
                    return {
                        "success": True, "written": False, "skipped": True,
                        "path": file_path, "bytes_written": 0, "error": None,
                    }
        parent_dir = os.path.dirname(os.path.abspath(file_path))
        if parent_dir and create_dirs:
            os.makedirs(parent_dir, exist_ok=True)
        with open(file_path, "w", encoding=encoding, newline="") as fh:
            fh.write(content)
        return {
            "success": True, "written": True, "skipped": False,
            "path": file_path, "bytes_written": len(content.encode(encoding)),
            "error": None,
        }
    except (OSError, UnicodeError, ValueError) as exc:
        return {
            "success": False, "written": False, "skipped": False,
            "path": file_path, "bytes_written": 0, "error": str(exc),
        }


def run_py_compile_check(file_path: str, timeout: int = 30) -> Dict[str, Any]:
    """Run python -m py_compile pre-flight syntax check on file_path."""
    if not isinstance(file_path, str) or not file_path.strip():
        return {
            "success": False, "valid": False, "file_path": file_path,
            "returncode": -1, "stdout": "", "stderr": "",
            "error": "file_path must be a non-empty string",
        }
    if not os.path.isfile(file_path):
        return {
            "success": False, "valid": False, "file_path": file_path,
            "returncode": -1, "stdout": "", "stderr": "",
            "error": f"File not found: {file_path}",
        }
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "py_compile", file_path],
            capture_output=True, text=True, timeout=timeout,
        )
        if proc.returncode == 0:
            return {
                "success": True, "valid": True, "file_path": file_path,
                "returncode": 0, "stdout": proc.stdout,
                "stderr": proc.stderr, "error": None,
            }
        err_msg = (proc.stderr or proc.stdout or f"py_compile exited {proc.returncode}").strip()
        return {
            "success": False, "valid": False, "file_path": file_path,
            "returncode": proc.returncode, "stdout": proc.stdout,
            "stderr": proc.stderr, "error": err_msg,
        }
    except subprocess.TimeoutExpired:
        return {
            "success": False, "valid": False, "file_path": file_path,
            "returncode": -1, "stdout": "", "stderr": "",
            "error": f"py_compile timed out after {timeout}s",
        }
    except OSError as exc:
        return {
            "success": False, "valid": False, "file_path": file_path,
            "returncode": -1, "stdout": "", "stderr": "", "error": str(exc),
        }


def extract_fenced_code_blocks(
    text: str, language: Optional[str] = None
) -> Dict[str, Any]:
    """Extract markdown fenced code blocks, optionally filtered by language."""
    if not isinstance(text, str):
        return {"success": False, "blocks": [], "count": 0, "error": "text must be a string"}
    fence_re = re.compile(r"^\s*(`{3,}|~{3,})\s*([^\s`~]*)\s*$")
    blocks: List[Dict[str, Any]] = []
    in_fence = False
    fence_char, fence_len, block_lang, start_line = "", 0, "", 0
    buf: List[str] = []

    for lineno, line in enumerate(text.splitlines(), start=1):
        if not in_fence:
            match = fence_re.match(line)
            if match:
                in_fence = True
                fence_char = match.group(1)[0]
                fence_len = len(match.group(1))
                block_lang = match.group(2).lower()
                start_line = lineno
                buf = []
        else:
            stripped = line.strip()
            if stripped and set(stripped) == {fence_char} and len(stripped) >= fence_len:
                blocks.append({
                    "language": block_lang, "code": "\n".join(buf),
                    "start_line": start_line, "end_line": lineno,
                })
                in_fence = False
            else:
                buf.append(line)

    if in_fence:
        return {
            "success": False, "blocks": blocks, "count": len(blocks),
            "error": f"Unclosed code fence starting at line {start_line}",
        }
    if language:
        target = language.strip().lower()
        blocks = [b for b in blocks if b["language"] == target]
    return {"success": True, "blocks": blocks, "count": len(blocks), "error": None}


def parse_search_replace_blocks(diff_text: str) -> Dict[str, Any]:
    """Parse <<<<<<< SEARCH / ======= / >>>>>>> REPLACE blocks safely."""
    if not isinstance(diff_text, str):
        return {"success": False, "blocks": [], "count": 0, "error": "diff_text must be a string"}
    blocks: List[Dict[str, Any]] = []
    state = "idle"
    search_lines: List[str] = []
    replace_lines: List[str] = []
    start_line = 0

    for lineno, line in enumerate(diff_text.splitlines(), start=1):
        marker = line.strip()
        if marker == "<<<<<<< SEARCH":
            if state != "idle":
                return {
                    "success": False, "blocks": blocks, "count": len(blocks),
                    "error": f"Unexpected SEARCH marker at line {lineno} while in '{state}'",
                }
            state, start_line, search_lines, replace_lines = "search", lineno, [], []
        elif marker == "=======":
            if state != "search":
                return {
                    "success": False, "blocks": blocks, "count": len(blocks),
                    "error": f"Unexpected ======= separator at line {lineno}",
                }
            state = "replace"
        elif marker == ">>>>>>> REPLACE":
            if state != "replace":
                return {
                    "success": False, "blocks": blocks, "count": len(blocks),
                    "error": f"Unexpected REPLACE marker at line {lineno}",
                }
            blocks.append({
                "search": "\n".join(search_lines),
                "replace": "\n".join(replace_lines),
                "start_line": start_line,
                "end_line": lineno,
            })
            state = "idle"
        elif state == "search":
            search_lines.append(line)
        elif state == "replace":
            replace_lines.append(line)

    if state != "idle":
        return {
            "success": False, "blocks": blocks, "count": len(blocks),
            "error": f"Unclosed search/replace block starting at line {start_line}",
        }
    return {"success": True, "blocks": blocks, "count": len(blocks), "error": None}


def validate_file_size_limits(
    content: str,
    max_lines: int = 300,
    max_func_lines: int = 100,
    lang: str = "python",
) -> Dict[str, Any]:
    """Validate total line count and per-function line limits."""
    if not isinstance(content, str):
        return {
            "success": False, "valid": False, "total_lines": 0,
            "max_lines": max_lines, "violations": [], "error": "content must be a string",
        }
    if max_lines <= 0 or max_func_lines <= 0:
        return {
            "success": False, "valid": False, "total_lines": 0,
            "max_lines": max_lines, "violations": [],
            "error": "max_lines and max_func_lines must be positive integers",
        }
    total_lines = len(content.splitlines())
    violations: List[Dict[str, Any]] = []
    if total_lines > max_lines:
        violations.append({
            "type": "file_size", "name": "<file>",
            "lines": total_lines, "limit": max_lines,
        })
    if lang == "python" and content.strip():
        try:
            tree = ast.parse(content)
        except SyntaxError as exc:
            return {
                "success": False, "valid": False, "total_lines": total_lines,
                "max_lines": max_lines, "violations": violations,
                "error": f"Syntax error at line {exc.lineno or 1}: {exc.msg}",
            }
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                dec_lines = [d.lineno for d in node.decorator_list]
                start = min([node.lineno] + dec_lines)
                end = getattr(node, "end_lineno", node.lineno)
                func_len = end - start + 1
                if func_len > max_func_lines:
                    violations.append({
                        "type": "function_size", "name": node.name,
                        "lines": func_len, "limit": max_func_lines, "lineno": start,
                    })
    valid = len(violations) == 0
    return {
        "success": True, "valid": valid, "total_lines": total_lines,
        "max_lines": max_lines, "violations": violations,
        "error": None if valid else f"{len(violations)} size limit violation(s)",
    }