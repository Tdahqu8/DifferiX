"""
tools/security_filter.py
------------------------
Detects dangerous code patterns before file write.

Two categories of patterns:

1. ALWAYS_BLOCKED — remote code execution, arbitrary deserialization,
   subprocess spawning. Blocked in every mode, including RSI.

2. CLEANUP_ALLOWED — file deletion primitives (unlink, rmtree, rmdir).
   Blocked in normal mode (agent editing a user project could destroy
   data). Allowed in RSI mode (framework itself needs these for
   rollback, temp cleanup, and atomic file operations).

Mode is selected by config.RSI_MODE. The filter never distinguishes
between new and existing files — a cleanup call is either allowed or
not, regardless of context. This is simpler and more predictable than
the previous diff-based approach.
"""

import ast
import re
from typing import List, Tuple

_LINE_COMMENT_RE = re.compile(r'^\s*(#|//)')
_TRAILING_COMMENT_RE = re.compile(r'\s+(#|//).*$')
_BLOCK_COMMENT_RE = re.compile(r'/\*.*?\*/', re.DOTALL)


# ---------------------------------------------------------------------------
# Category 1: always blocked. RCE, deserialization, process spawning.
# ---------------------------------------------------------------------------
ALWAYS_BLOCKED_PATTERNS = {
    r'\beval\s*\(': "eval()",
    r'\bexec\s*\(': "exec()",
    r'\bcompile\s*\(': "compile() (builds executable code objects)",
    r'__import__\s*\(': "__import__()",
    r'os\.popen\s*\(': "os.popen()",
    r'os\.exec[lv]p?e?\s*\(': "os.exec*() family",
    r'os\.spawn[lv]p?e?\s*\(': "os.spawn*() family",
    r'pty\.spawn\s*\(': "pty.spawn()",
    r'ctypes\.(CDLL|WinDLL|windll|cdll)': "ctypes native code loading",
    r'pickle\.loads?\s*\(': "pickle.load()/loads() (arbitrary deserialization)",
    r'marshal\.loads?\s*\(': "marshal.load()/loads()",
    r'getattr\s*\(\s*(os|subprocess|shutil|pickle|marshal|ctypes)\s*,': "dynamic getattr(module, ...) - possible obfuscated dangerous call",
    r'os\.system\s*\(': "os.system()",
    r'subprocess\.(run|Popen|call|check_output|check_call)\s*\(': "subprocess call",

    r'rm\s+-rf': "rm -rf",
    r'Remove-Item\s+.*-Recurse': "Remove-Item -Recurse",
    r'del\s+/s': "del /s",

    r'def\s+test_.*inspect': "Inspection test definition (test_inspect)",
    r'raise\s+Exception\s*\(.*(os\.listdir|glob)\s*\(': "Raising exception to dump workspace files",
    r'assert\s+False\s*,\s*.*(inspect|workspace)': "Intentional assert failure to dump workspace context",
    r'sys\.stderr\.write\s*\(.*open\s*\(': "Writing file content to stderr for log leakage",
    r'FILE_CONTENT_START': "Context dumping string markers detected",
}


# ---------------------------------------------------------------------------
# Category 2: cleanup primitives. Blocked in normal mode, allowed in RSI.
# ---------------------------------------------------------------------------
CLEANUP_PATTERNS = {
    r'os\.remove\s*\(': "os.remove()",
    r'os\.unlink\s*\(': "os.unlink()",
    r'os\.rmdir\s*\(': "os.rmdir()",
    r'os\.removedirs\s*\(': "os.removedirs()",
    r'shutil\.rmtree\s*\(': "shutil.rmtree()",
    r'\.unlink\s*\(': "Path.unlink()",
    r'shutil\.move\s*\(.*\.{2}[/\\]': "shutil.move with parent directory traversal",
    r'os\.rename\s*\(.*\.{2}[/\\]': "os.rename with parent directory traversal",
}


def _strip_comments_only(code: str) -> str:
    """Strip single-line and block comments. Triple-quoted strings intact."""
    cleaned = _BLOCK_COMMENT_RE.sub('', code)
    lines = []
    for line in cleaned.splitlines():
        if _LINE_COMMENT_RE.match(line):
            continue
        lines.append(_TRAILING_COMMENT_RE.sub('', line))
    return "\n".join(lines)


def _regex_scan(code: str, patterns: dict) -> List[str]:
    cleaned = _strip_comments_only(code)
    hits = []
    for pattern, label in patterns.items():
        if re.search(pattern, cleaned, re.IGNORECASE | re.DOTALL):
            hits.append(label)
    return hits


_DANGEROUS_CALL_NAMES = {"eval", "exec", "compile", "__import__"}

# AST-level dangerous call chains. Split into the same two categories.
_ALWAYS_BLOCKED_ATTR_CHAINS = {
    ("os", "system"): "os.system()",
    ("os", "popen"): "os.popen()",
    ("os", "execl"): "os.exec*() family",
    ("os", "execle"): "os.exec*() family",
    ("os", "execlp"): "os.exec*() family",
    ("os", "execlpe"): "os.exec*() family",
    ("os", "execv"): "os.exec*() family",
    ("os", "execve"): "os.exec*() family",
    ("os", "execvp"): "os.exec*() family",
    ("os", "execvpe"): "os.exec*() family",
    ("os", "spawnl"): "os.spawn*() family",
    ("os", "spawnv"): "os.spawn*() family",
    ("subprocess", "run"): "subprocess call",
    ("subprocess", "Popen"): "subprocess call",
    ("subprocess", "call"): "subprocess call",
    ("subprocess", "check_output"): "subprocess call",
    ("subprocess", "check_call"): "subprocess call",
    ("pickle", "load"): "pickle.load()/loads() (arbitrary deserialization)",
    ("pickle", "loads"): "pickle.load()/loads() (arbitrary deserialization)",
    ("marshal", "load"): "marshal.load()/loads()",
    ("marshal", "loads"): "marshal.load()/loads()",
    ("pty", "spawn"): "pty.spawn()",
    ("ctypes", "CDLL"): "ctypes native code loading",
    ("ctypes", "WinDLL"): "ctypes native code loading",
}

_CLEANUP_ATTR_CHAINS = {
    ("os", "remove"): "os.remove()",
    ("os", "unlink"): "os.unlink()",
    ("os", "rmdir"): "os.rmdir()",
    ("os", "removedirs"): "os.removedirs()",
    ("shutil", "rmtree"): "shutil.rmtree()",
}

_GETATTR_WATCHED_MODULES = {"os", "subprocess", "shutil", "pickle", "marshal", "ctypes"}


def _attr_chain(node: ast.AST) -> Tuple[str, ...]:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return tuple(reversed(parts))


def _ast_scan(code: str, allow_cleanup: bool) -> List[str]:
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError):
        return []

    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func

        if isinstance(func, ast.Name):
            if func.id in _DANGEROUS_CALL_NAMES:
                hits.append(f"{func.id}() (AST)")
                continue
            if func.id == "getattr" and node.args:
                first_arg = node.args[0]
                if isinstance(first_arg, ast.Name) and first_arg.id in _GETATTR_WATCHED_MODULES:
                    hits.append(f"dynamic getattr({first_arg.id}, ...) - possible obfuscated call (AST)")
                    continue

        if isinstance(func, ast.Attribute):
            chain = _attr_chain(func)
            if len(chain) >= 2:
                key = (chain[-2], chain[-1])
                if key in _ALWAYS_BLOCKED_ATTR_CHAINS:
                    hits.append(f"{_ALWAYS_BLOCKED_ATTR_CHAINS[key]} (AST)")
                elif not allow_cleanup and key in _CLEANUP_ATTR_CHAINS:
                    hits.append(f"{_CLEANUP_ATTR_CHAINS[key]} (AST)")

    return hits


def find_dangerous_code(code: str, allow_cleanup: bool = False) -> list:
    """
    Detect dangerous code patterns.

    Args:
        code: source text to scan.
        allow_cleanup: if True, file deletion primitives (unlink, rmtree,
            rmdir, remove) are permitted. Used in RSI mode where the
            framework itself contains legitimate cleanup code.

    Returns a deduplicated list of human-readable labels.
    """
    if not isinstance(code, str) or not code.strip():
        return []

    hits = []
    hits.extend(_regex_scan(code, ALWAYS_BLOCKED_PATTERNS))
    if not allow_cleanup:
        hits.extend(_regex_scan(code, CLEANUP_PATTERNS))
    hits.extend(_ast_scan(code, allow_cleanup))

    seen = set()
    deduped = []
    for h in hits:
        if h not in seen:
            seen.add(h)
            deduped.append(h)
    return deduped


def is_safe(code: str, allow_cleanup: bool = False) -> bool:
    return len(find_dangerous_code(code, allow_cleanup=allow_cleanup)) == 0