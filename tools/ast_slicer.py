"""
DifferiX AST Slicer and Context Freshness Module

Provides structural symbol map generation, AST-aware code slicing,
and file state freshness verification for large files.
"""

import ast
import hashlib
import os
from typing import Any, Dict, List, Optional


def generate_symbol_map(content: str, lang: str = "python") -> Dict[str, Any]:
    """
    Generates an abstract structural symbol map with line ranges for functions and classes.
    """
    if not content or lang != "python":
        return {"symbols": [], "map_text": "[empty or unsupported language]", "error": None}

    try:
        tree = ast.parse(content)
    except SyntaxError as e:
        return {"symbols": [], "map_text": "", "error": f"SyntaxError: {e.msg} at line {e.lineno}"}

    symbols = []
    map_lines = []

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            start_line = node.lineno
            end_line = getattr(node, "end_lineno", node.lineno)
            if node.decorator_list:
                start_line = min(d.lineno for d in node.decorator_list)

            sym_type = type(node).__name__
            symbols.append({
                "name": node.name,
                "type": sym_type,
                "lineno": start_line,
                "end_lineno": end_line,
            })

            if isinstance(node, ast.ClassDef):
                map_lines.append(f"class {node.name} (lines {start_line}-{end_line}):")
                for subnode in node.body:
                    if isinstance(subnode, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        sub_start = subnode.lineno
                        sub_end = getattr(subnode, "end_lineno", subnode.lineno)
                        if subnode.decorator_list:
                            sub_start = min(d.lineno for d in subnode.decorator_list)
                        symbols.append({
                            "name": f"{node.name}.{subnode.name}",
                            "type": type(subnode).__name__,
                            "lineno": sub_start,
                            "end_lineno": sub_end,
                        })
                        map_lines.append(f"    - def {subnode.name}() (lines {sub_start}-{sub_end})")
            else:
                map_lines.append(f"def {node.name}() (lines {start_line}-{end_line})")

    return {
        "symbols": symbols,
        "map_text": "\n".join(map_lines),
        "error": None,
    }


def smart_ast_slice(content: str, target_file: str, max_lines: int = 150, hints: str = "") -> str:
    """
    Slices code intelligently based on symbol matches with hints or fallback to symbol map + head/tail.
    """
    if not content:
        return "[empty]"

    lines = content.splitlines()
    total_lines = len(lines)
    if total_lines <= max_lines:
        return content

    symbol_map_res = generate_symbol_map(content)
    symbols = symbol_map_res.get("symbols", [])

    matched_ranges = []
    hints_lower = (hints or "").lower()

    for sym in symbols:
        name_lower = sym["name"].lower()
        if name_lower in hints_lower or any(
            part and part in hints_lower for part in name_lower.split(".")
        ):
            matched_ranges.append((sym["lineno"], sym["end_lineno"]))

    if matched_ranges:
        selected_lines = set()
        for start, end in matched_ranges:
            buf_start = max(1, start - 10)
            buf_end = min(total_lines, end + 10)
            for idx in range(buf_start, buf_end + 1):
                selected_lines.add(idx)

        sorted_indices = sorted(list(selected_lines))
        result_chunks = []
        last_idx = None

        for idx in sorted_indices:
            if last_idx is not None and idx > last_idx + 1:
                result_chunks.append(
                    f"\n# ... [omitted lines {last_idx + 1} to {idx - 1}] ...\n"
                )
            result_chunks.append(lines[idx - 1])
            last_idx = idx

        sliced_result = "\n".join(result_chunks)
        if len(sliced_result.splitlines()) <= max_lines * 1.5:
            return sliced_result

    # Fallback: Symbol Map + Head/Tail
    head_size = max_lines // 2
    tail_size = max_lines - head_size

    header = (
        f"=== SYMBOL MAP ===\n{symbol_map_res.get('map_text', '')}\n"
        f"=== CODE SLICE (Truncated) ==="
    )
    head_code = "\n".join(lines[:head_size])
    tail_code = "\n".join(lines[-tail_size:])

    return f"{header}\n{head_code}\n\n# ... [omitted middle lines] ...\n\n{tail_code}"


def verify_context_freshness(
    file_path: str, expected_hash: Optional[str] = None
) -> Dict[str, Any]:
    """
    Verifies that the file on disk has not changed unexpectedly since last read.

    Returns:
        {"fresh": bool, "hash": str|None, "error": str|None}
    """
    if not file_path or not os.path.exists(file_path):
        return {"fresh": True, "hash": None, "error": "File does not exist"}

    try:
        with open(file_path, "rb") as f:
            content = f.read()
        current_hash = hashlib.sha256(content).hexdigest()
    except OSError as e:
        return {"fresh": False, "hash": None, "error": str(e)}

    if expected_hash is None:
        return {"fresh": True, "hash": current_hash, "error": None}

    if current_hash != expected_hash:
        return {
            "fresh": False,
            "hash": current_hash,
            "error": (
                f"Hash mismatch: expected {expected_hash[:12]}..., "
                f"got {current_hash[:12]}..."
            ),
        }

    return {"fresh": True, "hash": current_hash, "error": None}