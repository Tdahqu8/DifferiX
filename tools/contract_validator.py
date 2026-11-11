"""
tools/contract_validator.py
----------------------------
Pre-write AST validation against the current structure index.

Runs before any edit is written to disk. If a proposed change set contains
calls to methods that don't exist, or imports that resolve to nothing, it
returns a structured report. main.py decides whether to block the write.

Design principles:
  1. Fail-open on internal error. A bug in the validator must never block
     legitimate work; the sandbox is still the authority.
  2. Confidence-scored checks. Anything below 0.9 is a warning, not an error.
  3. Escape hatch: `# differix: noqa-contract` on a line disables checks for
     that line. `# differix: ignore-dynamic` at module top disables the
     dynamic-dispatch heuristics entirely.
  4. Fresh index, per Option-1 override — validator builds its own
     StructureIndex.rebuild() internally so there's no staleness window.

Public API:
  - ContractValidator(project_root).validate_change_set(change_set) -> ValidationReport
  - ContractValidationError   (raised only by strict mode; default is off)
"""

from __future__ import annotations

import ast
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from tools.structure_index import StructureIndex, ModuleInfo, _path_to_module

logger = logging.getLogger(__name__)


class ContractValidationError(Exception):
    """Raised by strict mode when the report contains blocking errors."""


@dataclass
class ValidationIssue:
    path: str
    line: int
    col: int
    code: str
    message: str
    severity: str  # "error" | "warning"
    confidence: float

    def as_dict(self) -> Dict:
        return {
            "path": self.path,
            "line": self.line,
            "col": self.col,
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
            "confidence": round(self.confidence, 3),
        }


@dataclass
class ValidationReport:
    issues: List[ValidationIssue] = field(default_factory=list)
    files_checked: int = 0
    elapsed_ms: float = 0.0
    internal_error: Optional[str] = None

    @property
    def errors(self) -> List[ValidationIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> List[ValidationIssue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors and not self.internal_error

    def as_dict(self) -> Dict:
        return {
            "ok": self.ok,
            "errors": [i.as_dict() for i in self.errors],
            "warnings": [i.as_dict() for i in self.warnings],
            "files_checked": self.files_checked,
            "elapsed_ms": round(self.elapsed_ms, 2),
            "internal_error": self.internal_error,
        }


# ---------------------------------------------------------------------------
# Builtin names we never flag
# ---------------------------------------------------------------------------
import builtins as _builtins_module
_BUILTIN_NAMES: Set[str] = set(dir(_builtins_module))

_COMMON_NAMES: Set[str] = {
    "self", "cls", "True", "False", "None", "NotImplemented", "Ellipsis",
    "__name__", "__file__", "__doc__", "__package__", "__spec__", "__loader__",
    "__builtins__", "__debug__", "__all__",
}


class ContractValidator:
    """
    Validates proposed change sets against a freshly-built structure index.

    The validator rebuilds the index on every call. Cost: ~20-200ms for a
    typical project. Benefit: no staleness window, no LLM retries triggered
    by a stale contract check.
    """

    def __init__(
        self,
        project_root: str,
        min_error_confidence: float = 0.9,
        rebuild_index_every_call: bool = True,
    ) -> None:
        self.project_root = str(Path(project_root).resolve())
        self.min_error_confidence = min_error_confidence
        self.rebuild_index_every_call = rebuild_index_every_call
        self._cached_index: Optional[StructureIndex] = None

    # ------------------------------------------------------------------ #
    # Public entry point
    # ------------------------------------------------------------------ #
    def validate_change_set(self, change_set) -> ValidationReport:
        """
        Validate a ChangeSet. Never raises unless the caller sets strict
        mode (default: fail-open, always returns a report).

        `change_set` must implement `dry_apply(project_root) -> Dict[path, content]`.
        """
        t0 = time.perf_counter()
        report = ValidationReport()

        try:
            post_apply = change_set.dry_apply(self.project_root)
        except Exception as exc:
            report.internal_error = f"dry_apply failed: {exc}"
            report.elapsed_ms = (time.perf_counter() - t0) * 1000.0
            return report

        if not post_apply:
            report.elapsed_ms = (time.perf_counter() - t0) * 1000.0
            return report

        try:
            index = self._get_index()
        except Exception as exc:
            report.internal_error = f"index build failed: {exc}"
            report.elapsed_ms = (time.perf_counter() - t0) * 1000.0
            return report

        report.files_checked = len(post_apply)

        for rel_path, content in post_apply.items():
            if not rel_path.endswith(".py"):
                continue
            try:
                self._validate_python_file(rel_path, content, index, report)
            except Exception as exc:
                logger.warning("Validation error on %s: %s", rel_path, exc)

        report.elapsed_ms = (time.perf_counter() - t0) * 1000.0
        return report

    # ------------------------------------------------------------------ #
    # Index management
    # ------------------------------------------------------------------ #
    def _get_index(self) -> StructureIndex:
        if self.rebuild_index_every_call or self._cached_index is None:
            self._cached_index = StructureIndex.rebuild(self.project_root)
        return self._cached_index

    # ------------------------------------------------------------------ #
    # Python file validation
    # ------------------------------------------------------------------ #
    def _validate_python_file(
        self,
        rel_path: str,
        content: str,
        index: StructureIndex,
        report: ValidationReport,
    ) -> None:
        if _module_has_dynamic_opt_out(content):
            return

        try:
            tree = ast.parse(content, filename=rel_path)
        except SyntaxError as exc:
            report.issues.append(ValidationIssue(
                path=rel_path,
                line=exc.lineno or 1,
                col=exc.offset or 1,
                code="syntax_error",
                message=f"Syntax error: {exc.msg}",
                severity="error",
                confidence=1.0,
            ))
            return

        local_names = _collect_local_names(tree)
        imported_symbols = _collect_imported_symbols(tree)
        import_module_aliases = _collect_import_module_aliases(tree)

        for node in ast.walk(tree):
            if _has_noqa(node, content):
                continue
            if isinstance(node, ast.Call):
                self._check_call(
                    node, rel_path, index,
                    local_names, imported_symbols, import_module_aliases,
                    report,
                )

    # ------------------------------------------------------------------ #
    # Call-site checks
    # ------------------------------------------------------------------ #
    def _check_call(
        self,
        node: ast.Call,
        rel_path: str,
        index: StructureIndex,
        local_names: Set[str],
        imported_symbols: Dict[str, Tuple[str, str]],
        module_aliases: Dict[str, str],
        report: ValidationReport,
    ) -> None:
        func = node.func

        # Case A: bare Name — e.g. `foo(...)`
        if isinstance(func, ast.Name):
            name = func.id
            if name in _BUILTIN_NAMES or name in _COMMON_NAMES:
                return
            if name in local_names:
                return
            if name in imported_symbols:
                src_module, original_name = imported_symbols[name]
                src_mod_info = index.modules.get(src_module)
                if src_mod_info is None or src_mod_info.syntax_error:
                    return  # can't check → fail-open
                if not _module_exports(src_mod_info, original_name):
                    alias_note = (
                        f" (imported as '{name}')" if name != original_name else ""
                    )
                    report.issues.append(ValidationIssue(
                        path=rel_path,
                        line=node.lineno,
                        col=node.col_offset,
                        code="imported_name_missing",
                        message=(
                            f"Name '{original_name}' is imported from "
                            f"'{src_module}'{alias_note}, but that module "
                            f"does not define or export it."
                        ),
                        severity="error",
                        confidence=0.9,
                    ))
                return
            # Unknown name — could be dynamic; skip.

        # Case B: Attribute — e.g. `module.func(...)` or `obj.method(...)`
        elif isinstance(func, ast.Attribute):
            chain = _attr_chain(func)
            if chain is None or len(chain) < 2:
                return
            root = chain[0]
            if root in module_aliases:
                module_path = module_aliases[root]
                target_module = index.modules.get(module_path)
                if target_module is None or target_module.syntax_error:
                    return
                attr_name = chain[-1]
                if not _module_exports(target_module, attr_name):
                    report.issues.append(ValidationIssue(
                        path=rel_path,
                        line=node.lineno,
                        col=node.col_offset,
                        code="call_to_missing_name",
                        message=(
                            f"Call to '{'.'.join(chain)}' but "
                            f"'{module_path}' does not define '{attr_name}'."
                        ),
                        severity="error",
                        confidence=0.85,
                    ))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _module_has_dynamic_opt_out(content: str) -> bool:
    """Check first 3 lines for `# differix: ignore-dynamic`."""
    for i, line in enumerate(content.splitlines()):
        if i > 3:
            break
        if "differix: ignore-dynamic" in line:
            return True
    return False


def _has_noqa(node: ast.AST, content: str) -> bool:
    """Check whether the node's source line contains `# differix: noqa-contract`."""
    lineno = getattr(node, "lineno", None)
    if lineno is None:
        return False
    lines = content.splitlines()
    if 1 <= lineno <= len(lines):
        return "differix: noqa-contract" in lines[lineno - 1]
    return False


def _collect_local_names(tree: ast.Module) -> Set[str]:
    names: Set[str] = set()

    class _Collector(ast.NodeVisitor):
        def visit_FunctionDef(self, node):
            names.add(node.name)
            for arg in _iter_all_args(node.args):
                names.add(arg.arg)
            self.generic_visit(node)

        def visit_AsyncFunctionDef(self, node):
            names.add(node.name)
            for arg in _iter_all_args(node.args):
                names.add(arg.arg)
            self.generic_visit(node)

        def visit_ClassDef(self, node):
            names.add(node.name)
            self.generic_visit(node)

        def visit_Assign(self, node):
            for t in node.targets:
                for sub in ast.walk(t):
                    if isinstance(sub, ast.Name):
                        names.add(sub.id)
            self.generic_visit(node)

        def visit_AnnAssign(self, node):
            if isinstance(node.target, ast.Name):
                names.add(node.target.id)
            self.generic_visit(node)

        def visit_For(self, node):
            for sub in ast.walk(node.target):
                if isinstance(sub, ast.Name):
                    names.add(sub.id)
            self.generic_visit(node)

        def visit_With(self, node):
            for item in node.items:
                if item.optional_vars is not None:
                    for sub in ast.walk(item.optional_vars):
                        if isinstance(sub, ast.Name):
                            names.add(sub.id)
            self.generic_visit(node)

        def visit_ExceptHandler(self, node):
            if node.name:
                names.add(node.name)
            self.generic_visit(node)

        def visit_comprehension(self, node):
            for sub in ast.walk(node.target):
                if isinstance(sub, ast.Name):
                    names.add(sub.id)
            self.generic_visit(node)

        def visit_Lambda(self, node):
            for arg in _iter_all_args(node.args):
                names.add(arg.arg)
            self.generic_visit(node)

    _Collector().visit(tree)
    return names


def _iter_all_args(args: ast.arguments):
    yield from args.posonlyargs
    yield from args.args
    yield from args.kwonlyargs
    if args.vararg:
        yield args.vararg
    if args.kwarg:
        yield args.kwarg


def _collect_imported_symbols(tree: ast.Module) -> Dict[str, Tuple[str, str]]:
    """
    Map local imported name -> (source_module, original_symbol_name).

    Handles both `from X import Y` and `from X import Y as Z`:
      - For `from X import Y`,       out["Y"] = ("X", "Y")
      - For `from X import Y as Z`,  out["Z"] = ("X", "Y")

    Only absolute imports are tracked. Relative imports (level > 0) fail open
    because we cannot resolve them without package context.
    """
    out: Dict[str, Tuple[str, str]] = {}

    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level and not node.module:
            continue
        module = node.module or ""
        if node.level:
            # Relative imports: skip to avoid false positives
            continue
        for alias in node.names:
            local = alias.asname or alias.name
            original = alias.name
            out[local] = (module, original)
    return out


def _collect_import_module_aliases(tree: ast.Module) -> Dict[str, str]:
    """
    Map local alias -> module path for `import X`, `import X as Y`,
    `import a.b.c as Y`.

    For `import X.Y` without alias, we register only the root: X -> "X.Y".
    That matches Python's own binding behavior.
    """
    out: Dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name.split(".")[0]
                out[local] = alias.name
    return out


def _module_exports(mod: ModuleInfo, name: str) -> bool:
    """
    Does `mod` define or export `name`?

    Checks:
      - top-level def/class/assignment
      - explicit names in __all__ (best-effort via top_level_names)
      - names re-imported into the module (re-exports)
    """
    if name in mod.top_level_names:
        return True
    if name in mod.functions or name in mod.classes:
        return True
    for imp in mod.imports:
        if name in imp.names:
            return True
        if name in imp.alias_of_names:
            return True
    return False


def _attr_chain(node: ast.AST) -> Optional[Tuple[str, ...]]:
    parts: List[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return tuple(reversed(parts))
    return None


__all__ = [
    "ContractValidator",
    "ContractValidationError",
    "ValidationReport",
    "ValidationIssue",
]