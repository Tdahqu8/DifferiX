import os
import subprocess
import sys
import ast
import logging
import importlib
import re
import io
import tokenize as tok

logger = logging.getLogger(__name__)


def format_code(file_path: str) -> dict:
    """Runs Black (for Python) or Prettier (for JS/CSS/HTML) if available."""
    if not os.path.exists(file_path):
        return {"ok": False, "reason": "File not found"}

    ext = os.path.splitext(file_path)[1].lower()

    try:
        if ext == ".py":
            cmd = ["black", "--quiet", file_path]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                return {"ok": True, "tool": "black", "fixed": True}
            return {"ok": False, "reason": result.stderr[:200]}

        elif ext in (".js", ".mjs", ".css", ".html", ".json"):
            cmd = ["npx", "--yes", "prettier", "--write", file_path]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if result.returncode == 0:
                return {"ok": True, "tool": "prettier", "fixed": True}
            return {"ok": False, "reason": result.stderr[:200]}

        return {"ok": True, "tool": "none", "fixed": False}

    except FileNotFoundError:
        return {"ok": True, "tool": "none", "fixed": False, "reason": "Formatter not installed"}
    except Exception as e:
        return {"ok": False, "reason": str(e)[:500]}


def lint_code(file_path: str) -> dict:
    """Runs Ruff (Python) or ESLint (JS) if available. Returns a list of errors."""
    if not os.path.exists(file_path):
        return {"ok": False, "errors": ["File not found"]}

    ext = os.path.splitext(file_path)[1].lower()
    errors = []

    try:
        if ext == ".py":
            cmd = ["ruff", "check", file_path, "--output-format=concise"]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if result.returncode != 0:
                errors = [line.strip() for line in result.stdout.splitlines() if line.strip()]

        elif ext in (".js", ".mjs"):
            cmd = ["npx", "--yes", "eslint", file_path]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if result.returncode != 0:
                errors = [line.strip() for line in result.stdout.splitlines() if line.strip()]

    except FileNotFoundError:
        return {"ok": True, "errors": [], "reason": "Linter not installed"}
    except Exception as e:
        logger.warning(f"Lint error: {e}")
        return {"ok": True, "errors": [], "reason": str(e)}

    return {"ok": len(errors) == 0, "errors": errors[:10]}


def _already_imported(content: str, module: str, name: str) -> bool:
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if not name:
                for alias in node.names:
                    if alias.name == module:
                        return True
        elif isinstance(node, ast.ImportFrom):
            if name and node.module == module:
                for alias in node.names:
                    if alias.name == name:
                        return True
    return False


def smart_insert_import(file_path: str, module: str, name: str) -> dict:
    """Inserts an import statement safely using AST, preserving comments/docstrings."""
    if not os.path.exists(file_path) or not file_path.endswith(".py"):
        return {"ok": False, "reason": "Only supports existing Python files"}

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        if _already_imported(content, module, name):
            return {"ok": True, "reason": "Already imported"}

        tree = ast.parse(content)
        lines = content.splitlines()

        last_import = None
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                last_import = node

        if name:
            import_str = f"from {module} import {name}"
        else:
            import_str = f"import {module}"

        if last_import is not None:
            insert_idx = getattr(last_import, "end_lineno", last_import.lineno)
        else:
            first_body = tree.body[0] if tree.body else None
            is_docstring = (
                isinstance(first_body, ast.Expr)
                and isinstance(getattr(first_body, "value", None), ast.Constant)
                and isinstance(first_body.value.value, str)
            )
            if is_docstring:
                insert_idx = first_body.end_lineno
            else:
                insert_idx = 0
                for i, line in enumerate(lines):
                    stripped = line.strip()
                    if not stripped or stripped.startswith("#"):
                        continue
                    insert_idx = i
                    break
                else:
                    insert_idx = len(lines)

        lines.insert(insert_idx, import_str)

        with open(file_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

        return {"ok": True, "reason": "Inserted successfully"}

    except Exception as e:
        return {"ok": False, "reason": str(e)}


def run_syntax_check(file_path: str) -> dict:
    if not os.path.exists(file_path):
        return {"ok": False, "reason": "File not found"}

    ext = os.path.splitext(file_path)[1].lower()
    try:
        if ext == ".py":
            result = subprocess.run(
                [sys.executable, "-m", "py_compile", file_path],
                timeout=10, capture_output=True, text=True,
            )
            return {
                "ok": result.returncode == 0,
                "reason": result.stderr[:500] if result.stderr else "Syntax OK",
            }
        elif ext in (".js", ".mjs", ".cjs"):
            result = subprocess.run(
                ["node", "--check", file_path],
                timeout=10, capture_output=True, text=True,
            )
            return {
                "ok": result.returncode == 0,
                "reason": result.stderr[:500] if result.stderr else "Syntax OK",
            }
        return {"ok": True, "reason": "Unsupported file type"}
    except FileNotFoundError:
        return {"ok": True, "reason": "Compiler not available"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "reason": "Timeout checking syntax"}
    except Exception as e:
        return {"ok": False, "reason": str(e)[:500]}


def fix_unclosed_brackets(file_path: str) -> dict:
    """Close unclosed brackets at EOF for **Python** files, using the tokenizer."""
    if not os.path.exists(file_path):
        return {"ok": False, "reason": "File not found"}

    if not file_path.endswith(".py"):
        return {"ok": False, "reason": "Only Python supported — use a formatter for JS"}

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            code = f.read()
    except Exception as e:
        return {"ok": False, "reason": str(e)}

    stack = []
    pairs = {")": "(", "]": "[", "}": "{"}
    try:
        for t in tok.generate_tokens(io.StringIO(code).readline):
            if t.type == tok.OP:
                if t.string in "([{":
                    stack.append(t.string)
                elif t.string in ")]}":
                    if stack and stack[-1] == pairs[t.string]:
                        stack.pop()
    except tok.TokenError:
        pass
    except Exception as e:
        return {"ok": False, "reason": f"tokenize failed: {e}"}

    if not stack:
        return {"ok": True, "fixed": []}

    closers = {"(": ")", "[": "]", "{": "}"}
    suffix = "".join(closers[b] for b in reversed(stack))

    if not code.endswith("\n"):
        code += "\n"

    try:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(code + suffix + "\n")
    except Exception as e:
        return {"ok": False, "reason": str(e)}

    return {"ok": True, "fixed": [f"Added {len(stack)} closer(s): {suffix}"]}


def insert_code_in_function(file_path: str, func_name: str, anchor: str, code: str) -> dict:
    """Insert code inside an existing Python function, after a specific anchor."""
    if not os.path.exists(file_path) or not file_path.endswith(".py"):
        return {"ok": False, "reason": "Only supports existing Python files"}

    if not func_name or not anchor or not code:
        return {"ok": False, "reason": "Missing required arguments (function, anchor, code)"}

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        tree = ast.parse(content)
        target_func = None
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == func_name:
                target_func = node
                break

        if target_func is None:
            return {"ok": False, "reason": f"Function '{func_name}' not found"}

        func_start = target_func.lineno - 1
        func_end = target_func.end_lineno
        func_lines = content.splitlines()[func_start:func_end]

        anchor_idx = None
        for i, line in enumerate(func_lines):
            if anchor in line:
                anchor_idx = i
                break

        if anchor_idx is None:
            return {"ok": False, "reason": f"Anchor '{anchor[:60]}' not found inside '{func_name}'"}

        anchor_line = func_lines[anchor_idx]
        anchor_indent = len(anchor_line) - len(anchor_line.lstrip())

        new_lines = code.splitlines()
        indented = [
            ((" " * anchor_indent) + ln if ln.strip() else ln) for ln in new_lines
        ]

        insert_at = anchor_idx + 1
        while insert_at < len(func_lines) and not func_lines[insert_at].strip():
            insert_at += 1

        for _ln in reversed(indented):
            func_lines.insert(insert_at, _ln)

        all_lines = content.splitlines()
        all_lines[func_start:func_end] = func_lines

        with open(file_path, "w", encoding="utf-8") as f:
            f.write("\n".join(all_lines) + "\n")

        return {"ok": True, "reason": f"Inserted {len(new_lines)} lines into '{func_name}'"}

    except Exception as e:
        return {"ok": False, "reason": str(e)}


def run_snippet(code_str: str, timeout: int = 2) -> dict:
    """Execute a Python code snippet with timeout and output capture."""
    if not code_str or not isinstance(code_str, str):
        return {"ok": False, "error": "Invalid code", "stdout": "", "stderr": ""}

    try:
        result = subprocess.run(
            [sys.executable, "-c", code_str],
            timeout=timeout, capture_output=True, text=True,
        )
        return {
            "ok": result.returncode == 0,
            "stdout": result.stdout[:500],
            "stderr": result.stderr[:500],
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout", "stdout": "", "stderr": ""}
    except Exception as e:
        return {"ok": False, "error": str(e)[:500], "stdout": "", "stderr": ""}


def check_imports(file_path: str) -> dict:
    """Check if all imports in a Python file can be resolved (no side-effects)."""
    if not os.path.exists(file_path) or not file_path.endswith(".py"):
        return {"ok": True, "missing": [], "reason": "Only Python files supported"}

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        tree = ast.parse(content)
        missing = []

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    try:
                        if importlib.util.find_spec(alias.name) is None:
                            missing.append(alias.name)
                    except (ImportError, ValueError, ModuleNotFoundError):
                        missing.append(alias.name)

            elif isinstance(node, ast.ImportFrom):
                if node.level > 0:
                    continue
                if not node.module:
                    continue
                try:
                    if importlib.util.find_spec(node.module) is None:
                        missing.append(node.module)
                        continue
                except (ImportError, ValueError, ModuleNotFoundError):
                    missing.append(node.module)
                    continue

                for alias in node.names:
                    if alias.name == "*":
                        continue
                    try:
                        mod = importlib.import_module(node.module)
                        if hasattr(mod, alias.name):
                            continue
                    except Exception:
                        pass
                    try:
                        if importlib.util.find_spec(f"{node.module}.{alias.name}") is None:
                            missing.append(f"{node.module}.{alias.name}")
                    except (ImportError, ValueError, ModuleNotFoundError):
                        missing.append(f"{node.module}.{alias.name}")

        return {"ok": len(missing) == 0, "missing": missing[:10]}

    except SyntaxError:
        return {"ok": False, "missing": [], "reason": "syntax error in file"}
    except Exception as e:
        return {"ok": False, "missing": [], "reason": str(e)[:500]}


def add_dependency(package_name: str) -> dict:
    """Attempt to add a dependency using available package managers."""
    if not re.fullmatch(r"^[a-zA-Z0-9_.-]+$", package_name):
        return {"ok": False, "manager": "none", "reason": "Invalid package name"}

    try:
        try:
            result = subprocess.run(
                ["uv", "add", package_name],
                timeout=60, capture_output=True, text=True,
            )
            if result.returncode == 0:
                return {"ok": True, "manager": "uv", "reason": result.stdout[:500]}
        except FileNotFoundError:
            pass

        try:
            result = subprocess.run(
                ["poetry", "add", package_name],
                timeout=60, capture_output=True, text=True,
            )
            if result.returncode == 0:
                return {"ok": True, "manager": "poetry", "reason": result.stdout[:500]}
        except FileNotFoundError:
            pass

        if os.path.exists("requirements.txt"):
            with open("requirements.txt", "a", encoding="utf-8") as f:
                f.write(f"\n{package_name}\n")
            return {"ok": True, "manager": "requirements.txt",
                    "reason": "Appended to requirements.txt"}

        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", package_name],
            timeout=60, capture_output=True, text=True,
        )
        return {
            "ok": result.returncode == 0,
            "manager": "pip",
            "reason": result.stdout[:500] if result.returncode == 0 else result.stderr[:500],
        }

    except subprocess.TimeoutExpired:
        return {"ok": False, "manager": "none", "reason": "timeout"}
    except Exception as e:
        return {"ok": False, "manager": "none", "reason": str(e)[:500]}


def rename_symbol(file_path: str, old_name: str, new_name: str) -> dict:
    """
    Renames a Python symbol (function/class/variable) using AST parsing.
    Validates identifiers and returns change count or error.
    """
    if not os.path.exists(file_path) or not file_path.endswith('.py'):
        return {"ok": False, "reason": "Only supports existing Python files"}

    if not re.fullmatch(r'[a-zA-Z_][a-zA-Z0-9_]*', old_name):
        return {"ok": False, "reason": f"Invalid old name '{old_name}'"}
    if not re.fullmatch(r'[a-zA-Z_][a-zA-Z0-9_]*', new_name):
        return {"ok": False, "reason": f"Invalid new name '{new_name}'"}

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        tree = ast.parse(content)
        changes = 0

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name == old_name:
                node.name = new_name
                changes += 1
            elif isinstance(node, ast.Name) and node.id == old_name:
                node.id = new_name
                changes += 1

        if changes == 0:
            return {"ok": False, "reason": f"Symbol '{old_name}' not found"}

        new_content = ast.unparse(tree)
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(new_content)

        return {"ok": True, "changes": changes}

    except SyntaxError as e:
        return {"ok": False, "reason": f"Syntax error: {str(e)}"}
    except Exception as e:
        logger.warning(f"Rename error: {e}")
        return {"ok": False, "reason": str(e)[:500]}


def delete_function(file_path: str, func_name: str) -> dict:
    """
    Deletes a Python function using AST parsing.
    Returns success status or error if function not found.
    """
    if not os.path.exists(file_path) or not file_path.endswith('.py'):
        return {"ok": False, "reason": "Only supports existing Python files"}

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        tree = ast.parse(content)

        for i, node in enumerate(tree.body):
            if isinstance(node, ast.FunctionDef) and node.name == func_name:
                del tree.body[i]
                new_content = ast.unparse(tree)
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(new_content)
                return {"ok": True, "deleted": func_name}

        return {"ok": False, "reason": f"Function '{func_name}' not found"}

    except SyntaxError as e:
        return {"ok": False, "reason": f"Syntax error: {str(e)}"}
    except Exception as e:
        logger.warning(f"Delete function error: {e}")
        return {"ok": False, "reason": str(e)[:500]}