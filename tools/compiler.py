"""
tools/compiler.py
-----------------
Universal multi-language syntax / build verification for VegeX.

Public API (compatible with the previous implementation):
    run_universal_build(project_path, language, target_file) -> (bool, str)

Design contract:
  - Every external tool invocation has a hard timeout.
  - Missing toolchains return success=True with skipped=True — the pipeline
    continues, the reviewer sees "SKIPPED_NO_TOOLCHAIN" in the log.
  - Diagnostics are extracted as structured records so the reviewer can
    cite file:line:column in its verdict.
  - Python syntax is verified in-process (compile() with dont_inherit=True).
  - C# project-wide build path is preserved unchanged.
  - CSS and HTML get heuristic truncation checks (LLMs love to cut off halfway).
"""

from __future__ import annotations

import ast
import glob
import json
import os
import re
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

try:
    import yaml as _yaml  # type: ignore
    _HAS_YAML = True
except ImportError:
    _yaml = None
    _HAS_YAML = False

# Timeouts (seconds)
TIMEOUT_FAST = 15
TIMEOUT_CC = 30
TIMEOUT_TSC = 90
TIMEOUT_CARGO = 180
TIMEOUT_DOTNET = 240

@dataclass(frozen=True)
class Diagnostic:
    file: str
    line: int
    column: int
    message: str
    severity: str  # "error" | "warning" | "note"

@dataclass(frozen=True)
class CompilerResult:
    success: bool
    language: str
    file_path: str
    skipped: bool = False
    skip_reason: Optional[str] = None
    diagnostics: Tuple[Diagnostic, ...] = ()
    raw_stdout: str = ""
    raw_stderr: str = ""
    elapsed_ms: float = 0.0

    def as_log(self) -> str:
        """Human/LLM-readable summary."""
        tag = self.language.upper() if self.language else "UNKNOWN"
        if self.skipped:
            return (
                f"SUCCESS (SKIPPED): {self.skip_reason or 'validation skipped'} "
                f"for {os.path.basename(self.file_path) or self.file_path}"
            )
        if self.success:
            base = os.path.basename(self.file_path) or self.file_path
            return f"SUCCESS: {tag} validation passed for {base}"
        
        lines = [f"BUILD_FAILED ({tag}):"]
        for d in self.diagnostics:
            loc = f"{d.file}:{d.line}:{d.column}"
            lines.append(f"  {loc}: {d.severity}: {d.message}")
        if not self.diagnostics and self.raw_stderr.strip():
            lines.append("  " + self.raw_stderr.strip().splitlines()[0][:200])
        return "\n".join(lines)

def _run_subprocess(
    cmd: Sequence[str],
    cwd: Optional[str],
    timeout: int,
) -> Tuple[int, str, str, bool]:
    """
    Run a subprocess with a hard timeout and robust encoding fallback.
    """
    try:
        proc = subprocess.run(
            list(cmd),
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            shell=False,
        )
        return proc.returncode, proc.stdout or "", proc.stderr or "", False
    except subprocess.TimeoutExpired as exc:
        # FIX: TimeoutExpired has .output, not .stdout
        stdout = getattr(exc, "output", "") or ""
        stderr = getattr(exc, "stderr", "") or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", "replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", "replace")
        return -1, stdout, stderr + f"\n[TIMEOUT after {timeout}s]", True
    except OSError as exc:
        return -2, "", f"{exc}", False

# Diagnostic extraction patterns
_PAT_FILE_COL_MSG = re.compile(
    r"^(?P<file>[^:\s][^:]*?):(?P<line>\d+):(?P<col>\d+):\s+"
    r"(?P<sev>error|warning|note|fatal error):\s+(?P<msg>.+)$"
)
_PAT_FILE_MSG = re.compile(
    r"^(?P<file>[^:\s][^:]*?):(?P<line>\d+):\s+"
    r"(?P<sev>error|warning|note):\s+(?P<msg>.+)$"
)
_PAT_TSC = re.compile(
    r"^(?P<file>.+?)\((?P<line>\d+),(?P<col>\d+)\):\s+"
    r"(?P<sev>error|warning)\s+\S+:\s+(?P<msg>.+)$"
)
_PAT_LUAC = re.compile(
    r"^luac:\s+(?P<file>.+?):(?P<line>\d+):\s+(?P<msg>.+)$"
)
_PAT_NODE = re.compile(
    r"^(?P<file>/[^:\s].*?):(?P<line>\d+)\s*$"
)
_PAT_PYTB = re.compile(
    r'^\s*File "(?P<file>.+?)",\s+line\s+(?P<line>\d+)'
)
_PAT_GENERIC = re.compile(
    r"(?i)(?:^|\s)(error|warning|fatal error):\s*(?P<msg>.+)$"
)

_PARSER_CHAIN = (
    _PAT_FILE_COL_MSG,
    _PAT_TSC,
    _PAT_LUAC,
    _PAT_FILE_MSG,
    _PAT_NODE,
    _PAT_PYTB,
    _PAT_GENERIC,
)

def _extract_diagnostics(raw: str, default_severity: str = "error") -> List[Diagnostic]:
    """Best-effort structured extraction from any toolchain output."""
    diags: List[Diagnostic] = []
    for line in (raw or "").splitlines():
        line = line.rstrip()
        if not line:
            continue
        for pattern in _PARSER_CHAIN:
            m = pattern.match(line)
            if not m:
                continue
            gd = m.groupdict()
            diags.append(
                Diagnostic(
                    file=gd.get("file", "") or "",
                    line=int(gd.get("line", 0) or 0),
                    column=int(gd.get("col", 0) or 0),
                    message=(gd.get("msg") or "").strip(),
                    severity=gd.get("sev", default_severity).lower(),
                )
            )
            break
    return diags

# ---------------------------------------------------------------------------
# Per-language verifiers
# ---------------------------------------------------------------------------

def _verify_python(file_path: str) -> CompilerResult:
    t0 = time.perf_counter()
    try:
        with open(file_path, "rb") as f:
            source_bytes = f.read()
    except OSError as exc:
        return CompilerResult(False, "python", file_path, raw_stderr=f"FILE_ERROR: {exc}", elapsed_ms=(time.perf_counter() - t0) * 1000.0)

    try:
        source = source_bytes.decode("utf-8")
    except UnicodeDecodeError:
        source = source_bytes.decode("latin-1")

    try:
        ast.parse(source, filename=file_path)
        compile(source, file_path, "exec", dont_inherit=True)
        return CompilerResult(True, "python", file_path, elapsed_ms=(time.perf_counter() - t0) * 1000.0)
    except (SyntaxError, ValueError) as exc:
        diag = Diagnostic(
            file=file_path,
            line=getattr(exc, "lineno", 1) or 1,
            column=getattr(exc, "offset", 0) or 0,
            message=f"{type(exc).__name__}: {getattr(exc, 'msg', str(exc))}",
            severity="error",
        )
        return CompilerResult(False, "python", file_path, diagnostics=(diag,), raw_stderr=str(exc), elapsed_ms=(time.perf_counter() - t0) * 1000.0)

def _verify_json(file_path: str) -> CompilerResult:
    t0 = time.perf_counter()
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            json.load(f)
        return CompilerResult(True, "json", file_path, elapsed_ms=(time.perf_counter() - t0) * 1000.0)
    except json.JSONDecodeError as exc:
        diag = Diagnostic(file_path, exc.lineno, exc.colno, f"JSONDecodeError: {exc.msg}", "error")
        return CompilerResult(False, "json", file_path, diagnostics=(diag,), elapsed_ms=(time.perf_counter() - t0) * 1000.0)
    except OSError as exc:
        return CompilerResult(False, "json", file_path, raw_stderr=f"FILE_ERROR: {exc}", elapsed_ms=(time.perf_counter() - t0) * 1000.0)

def _verify_xml(file_path: str) -> CompilerResult:
    t0 = time.perf_counter()
    try:
        ET.parse(file_path)
        return CompilerResult(True, "xml", file_path, elapsed_ms=(time.perf_counter() - t0) * 1000.0)
    except ET.ParseError as exc:
        line, col = getattr(exc, "position", (0, 0))
        diag = Diagnostic(file_path, line, col, f"XMLParseError: {exc}", "error")
        return CompilerResult(False, "xml", file_path, diagnostics=(diag,), elapsed_ms=(time.perf_counter() - t0) * 1000.0)
    except OSError as exc:
        return CompilerResult(False, "xml", file_path, raw_stderr=f"FILE_ERROR: {exc}", elapsed_ms=(time.perf_counter() - t0) * 1000.0)

def _verify_yaml(file_path: str) -> CompilerResult:
    t0 = time.perf_counter()
    if not _HAS_YAML:
        return CompilerResult(True, "yaml", file_path, skipped=True, skip_reason="SKIPPED_NO_TOOLCHAIN (PyYAML not installed)", elapsed_ms=(time.perf_counter() - t0) * 1000.0)
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            _yaml.safe_load(f)
        return CompilerResult(True, "yaml", file_path, elapsed_ms=(time.perf_counter() - t0) * 1000.0)
    except Exception as exc:
        mark = getattr(exc, "problem_mark", None)
        line = mark.line + 1 if mark else 0
        col = mark.column + 1 if mark else 0
        diag = Diagnostic(file_path, line, col, f"YAMLError: {exc}", "error")
        return CompilerResult(False, "yaml", file_path, diagnostics=(diag,), elapsed_ms=(time.perf_counter() - t0) * 1000.0)

def _verify_css(file_path: str) -> CompilerResult:
    """Heuristic CSS check to catch truncation (unclosed braces/comments)."""
    t0 = time.perf_counter()
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
            
        # 1. Check for unclosed comments (odd number of /* vs */)
        open_comments = content.count("/*")
        close_comments = content.count("*/")
        if open_comments > close_comments:
            msg = f"CSS Syntax Error: Unclosed comment detected. Found {open_comments} '/*' but only {close_comments} '*/'."
            diag = Diagnostic(file_path, 0, 0, msg, "error")
            return CompilerResult(False, "css", file_path, diagnostics=(diag,), elapsed_ms=(time.perf_counter() - t0) * 1000.0)

        # 2. Strip comments to check braces
        content_no_comments = re.sub(r'/\*.*?\*/', '', content, flags=re.DOTALL)
        open_b = content_no_comments.count("{")
        close_b = content_no_comments.count("}")
        
        if open_b != close_b:
            msg = f"CSS Syntax Error: Unbalanced braces. {open_b} '{{' vs {close_b} '}}'. Output may be truncated."
            diag = Diagnostic(file_path, 0, 0, msg, "error")
            return CompilerResult(False, "css", file_path, diagnostics=(diag,), elapsed_ms=(time.perf_counter() - t0) * 1000.0)
            
        return CompilerResult(True, "css", file_path, elapsed_ms=(time.perf_counter() - t0) * 1000.0)
    except OSError as exc:
        return CompilerResult(False, "css", file_path, raw_stderr=f"FILE_ERROR: {exc}", elapsed_ms=(time.perf_counter() - t0) * 1000.0)
        
def _verify_html(file_path: str) -> CompilerResult:
    """Heuristic HTML check to catch severe truncation."""
    t0 = time.perf_counter()
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        # Strip comments and scripts/styles to avoid false positives
        content = re.sub(r'<!--.*?-->', '', content, flags=re.DOTALL)
        content = re.sub(r'<script.*?</script>', '', content, flags=re.DOTALL | re.IGNORECASE)
        content = re.sub(r'<style.*?</style>', '', content, flags=re.DOTALL | re.IGNORECASE)
        
        # Check for missing closing tags if opening tag was present
        if "<html" in content.lower() and "</html>" not in content.lower():
            msg = "HTML Syntax Error: Missing closing </html> tag. Output likely truncated."
            diag = Diagnostic(file_path, 0, 0, msg, "error")
            return CompilerResult(False, "html", file_path, diagnostics=(diag,), elapsed_ms=(time.perf_counter() - t0) * 1000.0)
            
        open_divs = len(re.findall(r'<div\b', content, re.IGNORECASE))
        close_divs = len(re.findall(r'</div>', content, re.IGNORECASE))
        if open_divs != close_divs:
            msg = f"HTML Syntax Error: Unbalanced <div> tags. {open_divs} '<div' vs {close_divs} '</div>'. Output may be truncated."
            diag = Diagnostic(file_path, 0, 0, msg, "error")
            return CompilerResult(False, "html", file_path, diagnostics=(diag,), elapsed_ms=(time.perf_counter() - t0) * 1000.0)

        return CompilerResult(True, "html", file_path, elapsed_ms=(time.perf_counter() - t0) * 1000.0)
    except OSError as exc:
        return CompilerResult(False, "html", file_path, raw_stderr=f"FILE_ERROR: {exc}", elapsed_ms=(time.perf_counter() - t0) * 1000.0)

def _verify_with_subprocess(
    file_path: str,
    language: str,
    cmd: Sequence[str],
    cwd: Optional[str],
    timeout: int,
    tool_name: str,
) -> CompilerResult:
    t0 = time.perf_counter()
    if not shutil.which(cmd[0]):
        return CompilerResult(True, language, file_path, skipped=True, skip_reason=f"SKIPPED_NO_TOOLCHAIN ({tool_name} not on PATH)", elapsed_ms=(time.perf_counter() - t0) * 1000.0)

    rc, out, err, timed_out = _run_subprocess(cmd, cwd=cwd, timeout=timeout)
    elapsed = (time.perf_counter() - t0) * 1000.0

    if timed_out:
        diag = Diagnostic(file_path, 0, 0, f"{tool_name} timed out after {timeout}s", "error")
        return CompilerResult(False, language, file_path, raw_stdout=out, raw_stderr=err, diagnostics=(diag,), elapsed_ms=elapsed)

    if rc == 0:
        return CompilerResult(True, language, file_path, raw_stdout=out, raw_stderr=err, elapsed_ms=elapsed)

    combined = (out or "") + "\n" + (err or "")
    diags = tuple(_extract_diagnostics(combined))
    if not diags:
        diags = (Diagnostic(file_path, 0, 0, (err or out or f"{tool_name} exited with code {rc}").strip()[:400], "error"),)
    return CompilerResult(False, language, file_path, diagnostics=diags, raw_stdout=out, raw_stderr=err, elapsed_ms=elapsed)

def _verify_js(file_path: str, project_root: str) -> CompilerResult:
    return _verify_with_subprocess(file_path, "javascript", ["node", "--check", file_path], project_root, TIMEOUT_FAST, "node")

def _verify_ts(file_path: str, project_root: str) -> CompilerResult:
    tsconfig = _find_ancestor_file(file_path, "tsconfig.json", project_root)
    if tsconfig:
        cmd = ["tsc", "--noEmit", "--project", tsconfig]
    else:
        cmd = ["tsc", "--noEmit", "--skipLibCheck", "--target", "esnext", "--module", "esnext", "--moduleResolution", "bundler", "--allowJs", file_path]
    return _verify_with_subprocess(file_path, "typescript", cmd, project_root, TIMEOUT_TSC, "tsc")

def _verify_lua(file_path: str, project_root: str) -> CompilerResult:
    for candidate in ("luac", "luac5.4", "luac5.3", "luac5.1"):
        if shutil.which(candidate):
            return _verify_with_subprocess(file_path, "lua", [candidate, "-p", file_path], project_root, TIMEOUT_FAST, candidate)
    return CompilerResult(True, "lua", file_path, skipped=True, skip_reason="SKIPPED_NO_TOOLCHAIN (luac not on PATH)")

def _verify_luau(file_path: str, project_root: str) -> CompilerResult:
    for candidate in ("luau-analyze", "luau_analyze"):
        if shutil.which(candidate):
            return _verify_with_subprocess(file_path, "luau", [candidate, file_path], project_root, TIMEOUT_FAST, candidate)
    return CompilerResult(True, "luau", file_path, skipped=True, skip_reason="SKIPPED_NO_TOOLCHAIN (luau-analyze not on PATH)")

def _verify_rust(file_path: str, project_root: str) -> CompilerResult:
    cargo_toml = _find_ancestor_file(file_path, "Cargo.toml", project_root)
    if cargo_toml:
        cargo = shutil.which("cargo")
        if not cargo:
            return CompilerResult(True, "rust", file_path, skipped=True, skip_reason="SKIPPED_NO_TOOLCHAIN (cargo not on PATH)")
        cargo_dir = os.path.dirname(cargo_toml)
        rc, out, err, timed_out = _run_subprocess([cargo, "check", "--message-format=json"], cwd=cargo_dir, timeout=TIMEOUT_CARGO)
        return _parse_cargo_result(rc, out, err, timed_out, file_path)

    rustc = shutil.which("rustc")
    if not rustc:
        return CompilerResult(True, "rust", file_path, skipped=True, skip_reason="SKIPPED_NO_TOOLCHAIN (rustc not on PATH)")
    return _verify_with_subprocess(file_path, "rust", [rustc, "--edition", "2021", "--crate-type", "lib", "--emit", "metadata", "--error-format", "short", file_path], project_root, TIMEOUT_FAST, "rustc")

def _parse_cargo_result(rc: int, out: str, err: str, timed_out: bool, target_file: str) -> CompilerResult:
    t0 = time.perf_counter()
    if timed_out:
        diag = Diagnostic(target_file, 0, 0, f"cargo check timed out after {TIMEOUT_CARGO}s", "error")
        return CompilerResult(False, "rust", target_file, raw_stdout=out, raw_stderr=err, diagnostics=(diag,), elapsed_ms=(time.perf_counter() - t0) * 1000.0)

    diags: List[Diagnostic] = []
    for line in (out or "").splitlines():
        if not line.strip().startswith("{"): continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("reason") != "compiler-message": continue
        msg = obj.get("message") or {}
        level = str(msg.get("level", "error")).lower()
        if level in ("note", "help"): continue
        spans = msg.get("spans") or []
        if spans:
            span = spans[0]
            diags.append(Diagnostic(span.get("file_name", target_file), int(span.get("line_start", 0) or 0), int(span.get("column_start", 0) or 0), (msg.get("message") or "").strip()[:400], level))
        else:
            diags.append(Diagnostic(target_file, 0, 0, (msg.get("message") or "").strip()[:400], level))

    if rc == 0:
        return CompilerResult(True, "rust", target_file, raw_stdout=out, raw_stderr=err, elapsed_ms=(time.perf_counter() - t0) * 1000.0)

    if not diags:
        diags = [Diagnostic(target_file, 0, 0, (err or "cargo check failed").strip()[:400], "error")]
    return CompilerResult(False, "rust", target_file, diagnostics=tuple(diags), raw_stdout=out, raw_stderr=err, elapsed_ms=(time.perf_counter() - t0) * 1000.0)

def _verify_c(file_path: str, project_root: str) -> CompilerResult:
    compiler = _first_available(("gcc", "clang", "cc"))
    if not compiler:
        return CompilerResult(True, "c", file_path, skipped=True, skip_reason="SKIPPED_NO_TOOLCHAIN (no C compiler on PATH)")
    return _verify_with_subprocess(file_path, "c", [compiler, "-fsyntax-only", "-I", project_root, "-I", os.path.dirname(file_path), file_path], project_root, TIMEOUT_CC, compiler)

def _verify_cpp(file_path: str, project_root: str) -> CompilerResult:
    compiler = _first_available(("g++", "clang++"))
    if not compiler:
        return CompilerResult(True, "cpp", file_path, skipped=True, skip_reason="SKIPPED_NO_TOOLCHAIN (no C++ compiler on PATH)")
    return _verify_with_subprocess(file_path, "cpp", [compiler, "-fsyntax-only", "-I", project_root, "-I", os.path.dirname(file_path), file_path], project_root, TIMEOUT_CC, compiler)

def _first_available(names: Sequence[str]) -> Optional[str]:
    for n in names:
        if shutil.which(n): return n
    return None

def _find_ancestor_file(file_path: str, filename: str, stop_at: Optional[str] = None) -> Optional[str]:
    current = os.path.dirname(os.path.abspath(file_path))
    stop = os.path.abspath(stop_at) if stop_at else None
    while True:
        candidate = os.path.join(current, filename)
        if os.path.isfile(candidate): return candidate
        if stop and current == stop: return None
        parent = os.path.dirname(current)
        if parent == current: return None
        current = parent

_PY_EXT = {".py", ".pyi", ".pyw"}

_DISPATCH: dict = {
    ".js":     _verify_js,
    ".mjs":    _verify_js,
    ".cjs":    _verify_js,
    ".ts":     _verify_ts,
    ".tsx":    _verify_ts,
    ".mts":    _verify_ts,
    ".lua":    _verify_lua,
    ".luau":   _verify_luau,
    ".rs":     _verify_rust,
    ".c":      _verify_c,
    ".h":      _verify_c,
    ".cpp":    _verify_cpp,
    ".cc":     _verify_cpp,
    ".cxx":    _verify_cpp,
    ".hpp":    _verify_cpp,
    ".hh":     _verify_cpp,
    ".hxx":    _verify_cpp,
    ".css":    _verify_css,
    ".html":   _verify_html,
    ".htm":    _verify_html,
}

def check_file(file_path: str, project_root: str = ".") -> CompilerResult:
    abs_path = os.path.abspath(file_path)
    if not os.path.exists(abs_path):
        return CompilerResult(False, "unknown", abs_path, raw_stderr=f"FILE_ERROR: Target file does not exist at {abs_path}")

    ext = os.path.splitext(abs_path)[1].lower()

    # In-process checkers
    if ext in _PY_EXT: return _verify_python(abs_path)
    if ext == ".json": return _verify_json(abs_path)
    if ext == ".xml": return _verify_xml(abs_path)
    if ext in (".yaml", ".yml"): return _verify_yaml(abs_path)

    # Subprocess / heuristic dispatch
    handler = _DISPATCH.get(ext)
    if handler:
        # Some handlers (like CSS/HTML) only take one arg
        try:
            return handler(abs_path, project_root)
        except TypeError:
            return handler(abs_path) # type: ignore

    # Unknown extension
    return CompilerResult(True, ext.lstrip(".") or "unknown", abs_path, skipped=True, skip_reason=f"SKIPPED_UNKNOWN_EXT ({ext or 'no extension'})")

def validate_xml_file(file_path: str) -> Tuple[bool, str]:
    result = _verify_xml(file_path)
    return result.success, result.as_log()

def validate_non_cs_file(file_path: str) -> Tuple[bool, str]:
    result = check_file(file_path)
    return result.success, result.as_log()

def build_csharp_assembly(project_path: str) -> Tuple[bool, str]:
    solutions = glob.glob(os.path.join(project_path, "*.sln"))
    projects = glob.glob(os.path.join(project_path, "**", "*.csproj"), recursive=True)
    target = solutions[0] if solutions else (projects[0] if projects else None)
    if not target: return True, "SUCCESS: No .csproj or .sln found. Skipping C# assembly build."

    if shutil.which("dotnet"):
        rc, out, err, timed_out = _run_subprocess(["dotnet", "build", target, "-c", "Release", "-v", "m"], cwd=project_path, timeout=TIMEOUT_DOTNET)
        if timed_out: return False, f"BUILD_FAILED: dotnet build timed out after {TIMEOUT_DOTNET}s"
        combined = (out + "\n" + err).strip()
        if rc == 0: return True, f"SUCCESS: C# assembly compiled successfully ({os.path.basename(target)})."
        clean_lines = [line.strip() for line in combined.splitlines() if "error" in line.lower() or "warning" in line.lower()]
        log = "\n".join(clean_lines[:15]) if clean_lines else combined[:500]
        return False, f"BUILD_FAILED:\n{log}"

    if shutil.which("msbuild"):
        rc, out, err, timed_out = _run_subprocess(["msbuild", target, "/p:Configuration=Release", "/v:m"], cwd=project_path, timeout=TIMEOUT_DOTNET)
        if timed_out: return False, f"BUILD_FAILED: msbuild timed out after {TIMEOUT_DOTNET}s"
        if rc == 0: return True, f"SUCCESS: Assembly built via MSBuild ({os.path.basename(target)})."
        return False, f"BUILD_FAILED (MSBuild):\n{(out or '')[:500]}"

    return False, "BUILD_ERROR: Neither 'dotnet' nor 'msbuild' found in PATH."

def run_universal_build(project_path: str, language: str, target_file: str = "") -> Tuple[bool, str]:
    ext = os.path.splitext(target_file)[1].lower() if target_file else ""
    if ext == ".cs": return build_csharp_assembly(project_path)
    if target_file:
        full_path = target_file if os.path.isabs(target_file) else os.path.join(project_path, target_file)
        result = check_file(full_path, project_root=project_path)
        return result.success, result.as_log()
    if language.lower() in ("c#", "csharp", "dotnet"): return build_csharp_assembly(project_path)
    return True, "SUCCESS: Verified workspace edit."

__all__ = [
    "Diagnostic",
    "CompilerResult",
    "check_file",
    "run_universal_build",
    "validate_xml_file",
    "validate_non_cs_file",
    "build_csharp_assembly",
]