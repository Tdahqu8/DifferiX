"""
DifferiX AST Operations - Core Refactoring Utilities

Provides structured AST validation, symbol inspection, source extraction,
and deterministic AST-based mutation primitives for Python code modifications.
All functions return structured dictionaries with explicit error handling.
"""

import json
import ast
import re
import difflib
from typing import Dict, List, Optional, Union


# ============================================================================
# Validation & Extraction (read-only operations)
# ============================================================================

def validate_ast_syntax(content: str, lang: str = "python") -> Dict[str, Union[bool, str]]:
    """
    Validates code syntax using AST parsing.

    Args:
        content: The code content to validate
        lang: The language ('python' or 'javascript')

    Returns:
        Dictionary with:
            - valid: bool indicating syntax validity
            - error: str error message if invalid
            - symbols: list of found symbols (if valid Python)
    """
    if not content:
        return {"valid": True, "error": None, "symbols": []}

    if lang == "python":
        try:
            tree = ast.parse(content)
            symbols = []
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)):
                    try:
                        start_line = node.lineno - 1
                        end_line = getattr(node, "end_lineno", node.lineno)
                        if node.decorator_list:
                            start_line = min(d.lineno - 1 for d in node.decorator_list)
                        source_lines = content.splitlines()[start_line:end_line]
                        symbols.append({
                            "name": node.name,
                            "type": type(node).__name__,
                            "lineno": node.lineno,
                            "end_lineno": end_line,
                            "source": "\n".join(source_lines),
                        })
                    except (AttributeError, IndexError):
                        continue
            return {"valid": True, "error": None, "symbols": symbols}
        except SyntaxError as e:
            return {
                "valid": False,
                "error": f"Syntax error at line {e.lineno or 1}: {e.msg}",
                "symbols": []
            }
    elif lang == "javascript":
        try:
            stack = []
            for i, char in enumerate(content):
                if char in '{[(':
                    stack.append((char, i))
                elif char in '}])':
                    if not stack:
                        return {
                            "valid": False,
                            "error": f"Unmatched {char} at position {i}",
                            "symbols": []
                        }
                    last, pos = stack.pop()
                    if (char == '}' and last != '{') or \
                       (char == ']' and last != '[') or \
                       (char == ')' and last != '('):
                        return {
                            "valid": False,
                            "error": f"Mismatched {last} at {pos} and {char} at {i}",
                            "symbols": []
                        }
            if stack:
                char, pos = stack[-1]
                return {
                    "valid": False,
                    "error": f"Unclosed {char} at position {pos}",
                    "symbols": []
                }
            return {"valid": True, "error": None, "symbols": []}
        except Exception as e:
            return {
                "valid": False,
                "error": f"JavaScript validation error: {str(e)}",
                "symbols": []
            }
    else:
        return {
            "valid": False,
            "error": f"Unsupported language: {lang}",
            "symbols": []
        }


def get_available_symbols(content: str, lang: str) -> Dict[str, Union[List[Dict], str]]:
    """Extracts available symbols (functions/classes) from code content."""
    if not content:
        return {"symbols": [], "error": None}

    if lang == "python":
        result = validate_ast_syntax(content, lang)
        if not result["valid"]:
            return {"symbols": [], "error": result["error"]}
        return {"symbols": result["symbols"], "error": None}
    elif lang == "javascript":
        symbols = []
        lines = content.splitlines()
        pattern = r"(?:(function\s+(\w+)\s*\()|(class\s+(\w+)\s*{)|(const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>)"
        for match in re.finditer(pattern, content):
            start_line = content.count("\n", 0, match.start()) + 1
            brace_level = 0
            pos = match.end()
            while pos < len(content):
                if content[pos] == "{":
                    brace_level += 1
                elif content[pos] == "}":
                    brace_level -= 1
                    if brace_level == 0:
                        break
                pos += 1
            end_line = content.count("\n", 0, pos) + 1
            source = "\n".join(lines[start_line - 1:end_line])
            symbols.append({
                "name": match.group(2) or match.group(4) or match.group(6),
                "type": "FunctionDef" if match.group(2) else "ClassDef",
                "lineno": start_line,
                "end_lineno": end_line,
                "source": source,
            })
        return {"symbols": symbols, "error": None}
    else:
        return {"symbols": [], "error": f"Unsupported language: {lang}"}


def extract_function_source(content: str, func_name: str) -> Dict[str, Union[str, None]]:
    """Extracts the complete source of a function by name."""
    if not content:
        return {"source": None, "error": "Empty content"}

    try:
        tree = ast.parse(content)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
                start_line = node.lineno - 1
                end_line = getattr(node, "end_lineno", node.lineno)
                if node.decorator_list:
                    start_line = min(d.lineno - 1 for d in node.decorator_list)
                source_lines = content.splitlines()[start_line:end_line]
                return {"source": "\n".join(source_lines), "error": None}
        return {"source": None, "error": f"Function '{func_name}' not found"}
    except SyntaxError as e:
        return {"source": None, "error": f"Syntax error at line {e.lineno or 1}: {e.msg}"}


def extract_class_source(content: str, class_name: str) -> Dict[str, Union[str, None]]:
    """Extracts the complete source of a class by name."""
    if not content:
        return {"source": None, "error": "Empty content"}

    try:
        tree = ast.parse(content)
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == class_name:
                start_line = node.lineno - 1
                end_line = getattr(node, "end_lineno", node.lineno)
                if node.decorator_list:
                    start_line = min(d.lineno - 1 for d in node.decorator_list)
                source_lines = content.splitlines()[start_line:end_line]
                return {"source": "\n".join(source_lines), "error": None}
        return {"source": None, "error": f"Class '{class_name}' not found"}
    except SyntaxError as e:
        return {"source": None, "error": f"Syntax error at line {e.lineno or 1}: {e.msg}"}


def validate_symbol_replacement(
    original_content: str,
    symbol_name: str,
    symbol_type: str,
    new_content: str
) -> Dict[str, Union[bool, str]]:
    """Validates if a symbol replacement maintains syntactic validity."""
    if not original_content:
        return {"valid": False, "error": "Original content is empty"}

    if symbol_type not in ("function", "class"):
        return {"valid": False, "error": f"Invalid symbol type: {symbol_type}"}

    orig_validation = validate_ast_syntax(original_content)
    if not orig_validation["valid"]:
        return {
            "valid": False,
            "error": f"Original content has syntax error: {orig_validation['error']}"
        }

    extractor = extract_function_source if symbol_type == "function" else extract_class_source
    orig_symbol = extractor(original_content, symbol_name)
    if orig_symbol["error"]:
        return {
            "valid": False,
            "error": f"Original symbol not found: {orig_symbol['error']}"
        }

    modified_content = original_content.replace(orig_symbol["source"], new_content)

    modified_validation = validate_ast_syntax(modified_content)
    if not modified_validation["valid"]:
        return {
            "valid": False,
            "error": f"Modified content has syntax error: {modified_validation['error']}"
        }

    return {"valid": True, "error": None}


def extract_json_object(text: str) -> Optional[str]:
    """Extracts the first valid JSON object string found in the text."""
    if not isinstance(text, str):
        return None

    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        return match.group(1)

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidate = text[start:end + 1]
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError:
            pass

    return None


# ============================================================================
# Deterministic AST Mutation Primitives
# ============================================================================

class ModificationPrimitive:
    """Base class for AST modification operations."""

    @staticmethod
    def get_symbols(content: str, lang: str = "python") -> list:
        """Return list of top-level symbols with line ranges."""
        if lang != "python":
            return []
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return []
        result = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                start = node.lineno
                if node.decorator_list:
                    start = min(d.lineno for d in node.decorator_list)
                result.append({
                    "name": node.name,
                    "type": type(node).__name__,
                    "lineno": start,
                    "end_lineno": getattr(node, "end_lineno", node.lineno),
                })
        return result


class ReplaceFunction(ModificationPrimitive):
    """Replace a Python function definition by AST node matching."""

    def __init__(self, name: str, new_body: str):
        self.name = name
        self.new_body = new_body

    def apply(self, content: str, lang: str) -> str:
        if lang != "python":
            raise ValueError("ReplaceFunction only supports Python")

        try:
            tree = ast.parse(content)
        except SyntaxError as e:
            raise ValueError(f"Target file syntax error: {e}")

        try:
            new_func = ast.parse(self.new_body)
            if not isinstance(new_func, ast.Module) or len(new_func.body) != 1:
                raise ValueError("Function body must contain exactly one function definition")
            if not isinstance(new_func.body[0], (ast.FunctionDef, ast.AsyncFunctionDef)):
                raise ValueError("Function body must be a function definition")
        except SyntaxError as e:
            raise ValueError(f"Invalid function body: {e}")

        lines = content.splitlines()
        modified = False

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == self.name:
                orig_lines = node.end_lineno - node.lineno
                new_lines = len(self.new_body.splitlines())

                # Anti-destructive sanity check (Phase 1.2)
                if orig_lines > 10 and new_lines < orig_lines * 0.6:
                    raise ValueError(
                        f"DESTRUCTIVE REPLACEMENT: '{self.name}' has {orig_lines} lines in original, "
                        f"but new_body has only {new_lines}. Include ALL existing lines or use insert_code."
                    )

                stub_markers = [
                    "# ... existing", "# rest of",
                    "# todo: keep", "# original implementation",
                    "# rest of the original",
                ]
                if any(m in self.new_body.lower() for m in stub_markers):
                    raise ValueError(
                        f"STUB DETECTED in replacement for '{self.name}'. "
                        f"Do not use placeholders. Use insert_code or include full body."
                    )

                start_line = node.lineno - 1
                end_line = node.end_lineno
                if node.decorator_list:
                    start_line = min(d.lineno - 1 for d in node.decorator_list)

                indent = " " * node.col_offset
                new_body_lines = [
                    indent + line if line.strip() else line
                    for line in self.new_body.splitlines()
                ]

                lines = lines[:start_line] + new_body_lines + lines[end_line:]
                modified = True
                break

        if not modified:
            available = [s["name"] for s in self.get_symbols(content, lang)]
            raise ValueError(
                f"Function '{self.name}' not found. Available: {available}"
            )

        return "\n".join(lines)


class ReplaceClass(ModificationPrimitive):
    """Replace a Python class definition by AST node matching."""

    def __init__(self, name: str, new_content: str):
        self.name = name
        self.new_content = new_content

    def apply(self, content: str, lang: str) -> str:
        if lang != "python":
            raise ValueError("ReplaceClass only supports Python")

        try:
            tree = ast.parse(content)
        except SyntaxError as e:
            raise ValueError(f"Target file syntax error: {e}")

        lines = content.splitlines()
        modified = False

        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == self.name:
                start_line = node.lineno - 1
                end_line = node.end_lineno
                if node.decorator_list:
                    start_line = min(d.lineno - 1 for d in node.decorator_list)

                indent = " " * node.col_offset
                new_body_lines = [
                    indent + line if line.strip() else line
                    for line in self.new_content.splitlines()
                ]

                lines = lines[:start_line] + new_body_lines + lines[end_line:]
                modified = True
                break

        if not modified:
            raise ValueError(f"Class '{self.name}' not found")

        return "\n".join(lines)


class InsertAfter(ModificationPrimitive):
    """Insert code after a specific anchor string."""

    def __init__(self, anchor: str, code: str):
        self.anchor = anchor
        self.code = code

    def apply(self, content: str, lang: str) -> str:
        if not self.anchor:
            raise ValueError("Empty anchor")
        if self.anchor not in content:
            raise ValueError(f"Anchor not found: '{self.anchor[:80]}'")
        idx = content.find(self.anchor)
        insert_pos = idx + len(self.anchor)
        return content[:insert_pos] + "\n" + self.code + content[insert_pos:]


# ============================================================================
# Search/Replace helpers (used as fallback in editor.apply_diff_blocks)
# ============================================================================

def flexible_find(content: str, search_block: str) -> tuple:
    """
    Find search_block in content with fallback to stripped and fuzzy matching.

    Returns:
        (start, end) if found, else (None, None).
    """
    if not search_block or not content:
        return None, None

    # 1. Exact match
    if search_block in content:
        idx = content.find(search_block)
        return idx, idx + len(search_block)

    # 2. Stripped-whitespace match
    search_lines_stripped = [ln.rstrip() for ln in search_block.splitlines()]
    content_lines_stripped = [ln.rstrip() for ln in content.splitlines()]
    search_stripped = "\n".join(search_lines_stripped)
    content_stripped = "\n".join(content_lines_stripped)

    if search_stripped and content_stripped.count(search_stripped) == 1:
        search_count = len(search_lines_stripped)
        original_lines = content.splitlines()
        for i in range(len(content_lines_stripped) - search_count + 1):
            window = content_lines_stripped[i:i + search_count]
            if "\n".join(window) == search_stripped:
                start = sum(len(ln) + 1 for ln in original_lines[:i])
                end = start + sum(
                    len(original_lines[j]) + 1
                    for j in range(i, i + search_count)
                ) - 1
                return start, end

    # 3. Fuzzy match (>= 85% similarity)
    search_count = len(search_lines_stripped)
    if search_count == 0 or search_count > len(content_lines_stripped):
        return None, None

    best_ratio = 0.0
    best_start_line = None
    for i in range(len(content_lines_stripped) - search_count + 1):
        window = content_lines_stripped[i:i + search_count]
        ratio = difflib.SequenceMatcher(
            None, search_stripped, "\n".join(window)
        ).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_start_line = i

    if best_ratio >= 0.85 and best_start_line is not None:
        original_lines = content.splitlines()
        start = sum(len(ln) + 1 for ln in original_lines[:best_start_line])
        end = start + sum(
            len(original_lines[j]) + 1
            for j in range(best_start_line, best_start_line + search_count)
        ) - 1
        return start, end

    return None, None


def looks_like_full_rewrite(diff_text: str) -> bool:
    """Detect if diff_text is a full file rewrite rather than a SEARCH/REPLACE diff."""
    if re.search(r"<{4,}\s*SEARCH", diff_text):
        return False
    fences = re.findall(r"```(?:\w+)?\n(.*?)```", diff_text, re.DOTALL)
    if len(fences) != 1:
        return False
    return fences[0].count("\n") >= 30