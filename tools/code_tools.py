import os
import subprocess
import sys
import ast
import logging

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
        logger.warning(f"Format error: {e}")
        return {"ok": True, "tool": "none", "fixed": False, "reason": str(e)}


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


def smart_insert_import(file_path: str, module: str, name: str) -> dict:
    """Inserts an import statement safely using AST, preserving all comments and formatting."""
    if not os.path.exists(file_path) or not file_path.endswith(".py"):
        return {"ok": False, "reason": "Only supports existing Python files"}

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
            
        if name:
            if f"from {module} import" in content and name in content:
                return {"ok": True, "reason": "Already imported"}
        else:
            if f"import {module}" in content:
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
            
        insert_idx = 0
        if last_import:
            insert_idx = getattr(last_import, 'end_lineno', last_import.lineno)
        else:
            for i, line in enumerate(lines):
                if not line.startswith("#"):
                    insert_idx = i
                    break
                    
        lines.insert(insert_idx, import_str)
        
        with open(file_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
            
        return {"ok": True, "reason": "Inserted successfully"}
        
    except Exception as e:
        return {"ok": False, "reason": str(e)}


def run_syntax_check(file_path: str) -> dict:
    """Check syntax of Python or JavaScript files using appropriate compilers."""
    if not os.path.exists(file_path):
        return {"ok": False, "reason": "File not found"}
        
    ext = os.path.splitext(file_path)[1].lower()
    try:
        if ext == ".py":
            result = subprocess.run(
                [sys.executable, "-m", "py_compile", file_path],
                timeout=10,
                capture_output=True,
                text=True
            )
            return {
                "ok": result.returncode == 0,
                "reason": result.stderr[:500] if result.stderr else "Syntax OK"
            }
        elif ext in (".js", ".mjs", ".cjs"):
            result = subprocess.run(
                ["node", "--check", file_path],
                timeout=10,
                capture_output=True,
                text=True
            )
            return {
                "ok": result.returncode == 0,
                "reason": result.stderr[:500] if result.stderr else "Syntax OK"
            }
        return {"ok": True, "reason": "Unsupported file type"}
    except FileNotFoundError:
        return {"ok": True, "reason": "Compiler not available"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "reason": "Timeout checking syntax"}
    except Exception as e:
        return {"ok": False, "reason": str(e)[:500]}


def fix_unclosed_brackets(file_path: str) -> dict:
    """Scans a JS/Python file and auto-closes unclosed brackets, braces, and parens at the end."""
    if not os.path.exists(file_path):
        return {"ok": False, "reason": "File not found"}
        
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            code = f.read()

        open_braces = code.count('{')
        close_braces = code.count('}')
        open_parens = code.count('(')
        close_parens = code.count(')')
        open_brackets = code.count('[')
        close_brackets = code.count(']')

        fixes = []
        if open_braces > close_braces:
            code += '\n' + '}' * (open_braces - close_braces)
            fixes.append(f"Added {open_braces - close_braces} '}}'")
        if open_parens > close_parens:
            code += ')' * (open_parens - close_parens)
            fixes.append(f"Added {open_parens - close_parens} ')'")
        if open_brackets > close_brackets:
            code += ']' * (open_brackets - close_brackets)
            fixes.append(f"Added {open_brackets - close_brackets} ']'")

        if fixes:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(code)
            return {"ok": True, "fixed": fixes}
        
        return {"ok": True, "fixed": []}
    except Exception as e:
        return {"ok": False, "reason": str(e)}


def insert_code_in_function(file_path: str, func_name: str, anchor: str, code: str) -> dict:
    """Insert code inside an existing Python function, after a specific anchor.

    Safer alternative to replace_function when adding lines to a large function.
    Requires only: function name, an anchor string inside the function, and the
    new code. Existing function logic is preserved untouched.
    """
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

        if not any(anchor in line for line in func_lines):
            return {"ok": False, "reason": f"Anchor '{anchor[:60]}' not found inside '{func_name}'"}

        anchor_idx = None
        for i, line in enumerate(func_lines):
            if anchor in line:
                anchor_idx = i
                break

        if anchor_idx is None:
            return {"ok": False, "reason": "Anchor not found"}

        anchor_line = func_lines[anchor_idx]
        anchor_indent = len(anchor_line) - len(anchor_line.lstrip())

        new_lines = code.splitlines()
        indented = [((" " * anchor_indent) + ln if ln.strip() else ln) for ln in new_lines]

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
    """Execute a Python code snippet safely with timeout and output capture."""
    if not code_str or not isinstance(code_str, str):
        return {"ok": False, "error": "Invalid code", "stdout": "", "stderr": ""}
        
    try:
        result = subprocess.run(
            [sys.executable, "-c", code_str],
            timeout=timeout,
            capture_output=True,
            text=True
        )
        return {
            "ok": result.returncode == 0,
            "stdout": result.stdout[:500],
            "stderr": result.stderr[:500]
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout", "stdout": "", "stderr": ""}
    except Exception as e:
        return {"ok": False, "error": str(e)[:500], "stdout": "", "stderr": ""}


def check_imports(file_path: str) -> dict:
    """Check if all imports in a Python file can be resolved."""
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
                        importlib.import_module(alias.name)
                    except ImportError:
                        missing.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.level > 0:  # Skip relative imports
                    continue
                try:
                    module = importlib.import_module(node.module)
                    for alias in node.names:
                        if not hasattr(module, alias.name):
                            missing.append(f"{node.module}.{alias.name}")
                except ImportError:
                    missing.append(node.module)
        
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
        # Try uv first
        try:
            result = subprocess.run(
                ["uv", "add", package_name],
                timeout=60,
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                return {"ok": True, "manager": "uv", "reason": result.stdout[:500]}
        except FileNotFoundError:
            pass
        
        # Try poetry next
        try:
            result = subprocess.run(
                ["poetry", "add", package_name],
                timeout=60,
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                return {"ok": True, "manager": "poetry", "reason": result.stdout[:500]}
        except FileNotFoundError:
            pass
        
        # Fallback to requirements.txt
        if os.path.exists("requirements.txt"):
            with open("requirements.txt", "a", encoding="utf-8") as f:
                f.write(f"\n{package_name}\n")
            return {"ok": True, "manager": "requirements.txt", "reason": "Appended to requirements.txt"}
        
        # Final fallback to pip
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", package_name],
            timeout=60,
            capture_output=True,
            text=True
        )
        return {
            "ok": result.returncode == 0,
            "manager": "pip",
            "reason": result.stdout[:500] if result.returncode == 0 else result.stderr[:500]
        }
        
    except subprocess.TimeoutExpired:
        return {"ok": False, "manager": "none", "reason": "timeout"}
    except Exception as e:
        return {"ok": False, "manager": "none", "reason": str(e)[:500]}