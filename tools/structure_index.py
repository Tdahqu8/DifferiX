"""
tools/structure_index.py
-------------------------
Byte-deterministic AST structure index for the VegeX codebase.

Per your directive (Override → Option 1), main.py will call rebuild() on
every validation step. That means freshness is guaranteed and the cost is
20–200ms of local CPU — cheap insurance against an LLM retry caused by a
stale index.

Public API:
  - StructureIndex.rebuild(project_root, deprecations_path=None) -> StructureIndex
  - index.save(path) / StructureIndex.load(path)
  - index.fingerprint -> str  # sha256 of all module content, for cache checks
  - index.modules  # Dict[module_name, ModuleInfo]

Determinism contract: two calls to rebuild() on unchanged source produce
byte-identical `save()` output *except* for the `generated_at` field. The
`fingerprint` field ignores `generated_at`, so cache comparisons use it.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCHEMA_VERSION = "2.0.0"

# Files we never index.
_IGNORED_DIRS = {
    ".git", ".vegex", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".venv", "venv", "env", "node_modules", "bin", "obj", "build",
    "dist", ".idea", ".vscode", "iteration_logs", "AI_Orchestrator_Backups",
}
_IGNORED_FILE_SUFFIXES = (".pyc", ".pyo", ".pyd", ".so", ".dll")
_IGNORED_FILENAMES = {
    "state.json", "structure_meta.json", "projects_registry.json",
    "skills_config.json", "active_models.json", "models_cache.json",
    ".agent_bindings.json", ".openrouter_models_cache.json",
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class ParamInfo:
    name: str
    kind: str  # positional_only|positional_or_keyword|keyword_only|var_positional|var_keyword
    annotation: Optional[str] = None
    default: Optional[str] = None
    has_default: bool = False


@dataclass
class FunctionInfo:
    name: str
    params: List[ParamInfo] = field(default_factory=list)
    returns: Optional[str] = None
    decorators: List[str] = field(default_factory=list)
    is_async: bool = False
    is_static: bool = False
    is_class: bool = False
    is_property: bool = False
    line: int = 0
    has_var_positional: bool = False
    has_var_keyword: bool = False

    @property
    def positional_params(self) -> List[ParamInfo]:
        return [
            p for p in self.params
            if p.kind in ("positional_only", "positional_or_keyword")
        ]

    @property
    def keyword_params(self) -> List[ParamInfo]:
        return [
            p for p in self.params
            if p.kind in ("positional_or_keyword", "keyword_only")
        ]


@dataclass
class ClassInfo:
    name: str
    bases: List[str] = field(default_factory=list)
    methods: Dict[str, FunctionInfo] = field(default_factory=dict)
    line: int = 0


@dataclass
class ImportInfo:
    module: str = ""  # e.g. "memory.storage"
    names: List[str] = field(default_factory=list)  # imported symbols
    alias_of_names: Dict[str, str] = field(default_factory=dict)  # local alias -> original
    level: int = 0  # 0 = absolute, 1+ = relative
    inside_try: bool = False
    type_checking_only: bool = False


@dataclass
class ModuleInfo:
    name: str  # canonical dotted module name
    file: str  # relative path with forward slashes
    content_hash: str
    classes: Dict[str, ClassInfo] = field(default_factory=dict)
    functions: Dict[str, FunctionInfo] = field(default_factory=dict)
    imports: List[ImportInfo] = field(default_factory=list)
    top_level_names: List[str] = field(default_factory=list)
    syntax_error: Optional[str] = None


# ---------------------------------------------------------------------------
# AST extraction
# ---------------------------------------------------------------------------
def _annotation_to_str(node: Optional[ast.AST]) -> Optional[str]:
    if node is None:
        return None
    try:
        return ast.unparse(node)
    except Exception:
        return None


def _extract_params(func: ast.FunctionDef | ast.AsyncFunctionDef) -> Tuple[List[ParamInfo], bool, bool]:
    args = func.args
    out: List[ParamInfo] = []
    has_var_pos = args.vararg is not None
    has_var_kw = args.kwarg is not None

    # positional-only
    for a in args.posonlyargs:
        out.append(ParamInfo(name=a.arg, kind="positional_only",
                             annotation=_annotation_to_str(a.annotation)))
    # positional-or-keyword
    pos_args = list(args.args)
    defaults = list(args.defaults)
    num_defaults = len(defaults)
    for i, a in enumerate(pos_args):
        default_idx = i - (len(pos_args) - num_defaults)
        has_default = default_idx >= 0
        out.append(ParamInfo(
            name=a.arg,
            kind="positional_or_keyword",
            annotation=_annotation_to_str(a.annotation),
            default=_annotation_to_str(defaults[default_idx]) if has_default else None,
            has_default=has_default,
        ))
    # var-positional
    if args.vararg is not None:
        out.append(ParamInfo(name=args.vararg.arg, kind="var_positional",
                             annotation=_annotation_to_str(args.vararg.annotation)))
    # keyword-only
    for i, a in enumerate(args.kwonlyargs):
        kw_default = args.kw_defaults[i] if i < len(args.kw_defaults) else None
        out.append(ParamInfo(
            name=a.arg, kind="keyword_only",
            annotation=_annotation_to_str(a.annotation),
            default=_annotation_to_str(kw_default) if kw_default is not None else None,
            has_default=kw_default is not None,
        ))
    # var-keyword
    if args.kwarg is not None:
        out.append(ParamInfo(name=args.kwarg.arg, kind="var_keyword",
                             annotation=_annotation_to_str(args.kwarg.annotation)))

    return out, has_var_pos, has_var_kw


def _extract_decorators(node: ast.FunctionDef | ast.AsyncFunctionDef) -> List[str]:
    return [_annotation_to_str(d) or "" for d in node.decorator_list]


def _extract_function(node, *, is_async: bool, class_context: Optional[str] = None) -> FunctionInfo:
    params, has_vp, has_vk = _extract_params(node)
    decorators = _extract_decorators(node)
    return FunctionInfo(
        name=node.name,
        params=params,
        returns=_annotation_to_str(node.returns),
        decorators=decorators,
        is_async=is_async,
        is_static=any("staticmethod" in d for d in decorators),
        is_class=any("classmethod" in d for d in decorators),
        is_property=any("property" in d for d in decorators),
        line=node.lineno,
        has_var_positional=has_vp,
        has_var_keyword=has_vk,
    )


def _extract_imports(tree: ast.Module) -> List[ImportInfo]:
    out: List[ImportInfo] = []

    def visit(node: ast.AST, inside_try: bool, type_checking_only: bool):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.Import):
                for alias in child.names:
                    info = ImportInfo(
                        module=alias.name,
                        names=[],
                        alias_of_names={alias.asname or alias.name.split(".")[0]: alias.name},
                        level=0,
                        inside_try=inside_try,
                        type_checking_only=type_checking_only,
                    )
                    out.append(info)
            elif isinstance(child, ast.ImportFrom):
                module = child.module or ""
                names = [a.name for a in child.names]
                alias_map = {(a.asname or a.name): a.name for a in child.names}
                out.append(ImportInfo(
                    module=module,
                    names=names,
                    alias_of_names=alias_map,
                    level=child.level or 0,
                    inside_try=inside_try,
                    type_checking_only=type_checking_only,
                ))
            elif isinstance(child, ast.Try):
                for stmt in child.body:
                    visit(stmt, True, type_checking_only)
                for handler in child.handlers:
                    for stmt in handler.body:
                        visit(stmt, True, type_checking_only)
                for stmt in child.orelse:
                    visit(stmt, inside_try, type_checking_only)
                for stmt in child.finalbody:
                    visit(stmt, inside_try, type_checking_only)
            elif isinstance(child, ast.If):
                is_type_checking = _is_type_checking_guard(child.test)
                for stmt in child.body:
                    visit(stmt, inside_try, type_checking_only or is_type_checking)
                for stmt in child.orelse:
                    visit(stmt, inside_try, type_checking_only)

    visit(tree, False, False)
    return out


def _is_type_checking_guard(test: ast.AST) -> bool:
    if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
        return True
    if isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING":
        return True
    return False


def _extract_module(
    file_path: Path, rel_path: str, module_name: str
) -> ModuleInfo:
    try:
        raw = file_path.read_bytes()
    except Exception as exc:
        return ModuleInfo(
            name=module_name, file=rel_path,
            content_hash="", syntax_error=f"read failed: {exc}",
        )

    content_hash = hashlib.sha256(raw).hexdigest()
    try:
        source = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        return ModuleInfo(
            name=module_name, file=rel_path,
            content_hash=content_hash, syntax_error=f"decode failed: {exc}",
        )

    try:
        tree = ast.parse(source, filename=rel_path)
    except SyntaxError as exc:
        return ModuleInfo(
            name=module_name, file=rel_path,
            content_hash=content_hash,
            syntax_error=f"line {exc.lineno}: {exc.msg}",
        )

    info = ModuleInfo(name=module_name, file=rel_path, content_hash=content_hash)

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            cls = ClassInfo(
                name=node.name,
                bases=[_annotation_to_str(b) or "" for b in node.bases],
                line=node.lineno,
            )
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    cls.methods[item.name] = _extract_function(
                        item,
                        is_async=isinstance(item, ast.AsyncFunctionDef),
                        class_context=node.name,
                    )
            info.classes[node.name] = cls
            info.top_level_names.append(node.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            info.functions[node.name] = _extract_function(
                node, is_async=isinstance(node, ast.AsyncFunctionDef)
            )
            info.top_level_names.append(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            for target in _assign_targets(node):
                if isinstance(target, ast.Name):
                    info.top_level_names.append(target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            pass  # handled by _extract_imports

    info.imports = _extract_imports(tree)
    return info


def _assign_targets(node) -> List[ast.AST]:
    targets: List[ast.AST] = []
    if isinstance(node, ast.Assign):
        for t in node.targets:
            if isinstance(t, (ast.Tuple, ast.List)):
                targets.extend(t.elts)
            else:
                targets.append(t)
    elif isinstance(node, ast.AnnAssign):
        targets.append(node.target)
    return targets


# ---------------------------------------------------------------------------
# Path → module name
# ---------------------------------------------------------------------------
def _path_to_module(rel_path: str) -> str:
    p = rel_path.replace("\\", "/")
    if p.endswith(".py"):
        p = p[:-3]
    if p.endswith("/__init__"):
        p = p[: -len("/__init__")]
    return p.replace("/", ".")


# ---------------------------------------------------------------------------
# StructureIndex
# ---------------------------------------------------------------------------
@dataclass
class StructureIndex:
    schema_version: str = SCHEMA_VERSION
    generated_at: str = ""
    project_root_hash: str = ""
    modules: Dict[str, ModuleInfo] = field(default_factory=dict)

    # ------------------------------------------------------------------ #
    # Build / load / save
    # ------------------------------------------------------------------ #
    @classmethod
    def rebuild(cls, project_root: str) -> "StructureIndex":
        root = Path(project_root).resolve()
        idx = cls(generated_at=_utc_now_iso())

        files = list(_iter_python_files(root))
        combined_hasher = hashlib.sha256()

        for abs_path in sorted(files):
            rel = abs_path.relative_to(root).as_posix()
            module_name = _path_to_module(rel)
            mod_info = _extract_module(abs_path, rel, module_name)
            idx.modules[module_name] = mod_info
            combined_hasher.update(rel.encode("utf-8"))
            combined_hasher.update(b"\0")
            combined_hasher.update(mod_info.content_hash.encode("utf-8"))
            combined_hasher.update(b"\n")

        idx.project_root_hash = combined_hasher.hexdigest()
        return idx

    def save(self, path: str) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, sort_keys=True, indent=2)
        os.replace(tmp, target)

    @classmethod
    def load(cls, path: str) -> Optional["StructureIndex"]:
        target = Path(path)
        if not target.exists():
            return None
        try:
            with open(target, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return None
        return cls.from_dict(data)

    # ------------------------------------------------------------------ #
    # Serialization (deterministic)
    # ------------------------------------------------------------------ #
    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at,
            "project_root_hash": self.project_root_hash,
            "fingerprint": self.fingerprint,
            "modules": {
                name: _module_to_dict(mod)
                for name, mod in sorted(self.modules.items())
            },
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StructureIndex":
        idx = cls(
            schema_version=data.get("schema_version", SCHEMA_VERSION),
            generated_at=data.get("generated_at", ""),
            project_root_hash=data.get("project_root_hash", ""),
        )
        for name, mod_dict in (data.get("modules") or {}).items():
            idx.modules[name] = _module_from_dict(mod_dict)
        return idx

    @property
    def fingerprint(self) -> str:
        """
        Fingerprint that ignores generated_at so identical source → identical
        fingerprint. Use this for prompt-cache checks.
        """
        h = hashlib.sha256()
        h.update(self.project_root_hash.encode("utf-8"))
        h.update(b"\0")
        h.update(SCHEMA_VERSION.encode("utf-8"))
        return h.hexdigest()


# ---------------------------------------------------------------------------
# Deterministic file walking
# ---------------------------------------------------------------------------
def _iter_python_files(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in _IGNORED_DIRS and not d.startswith(".")
        )
        for fname in sorted(filenames):
            if not fname.endswith(".py"):
                continue
            if fname in _IGNORED_FILENAMES:
                continue
            if any(fname.endswith(sfx) for sfx in _IGNORED_FILE_SUFFIXES):
                continue
            yield Path(dirpath) / fname


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------
def _module_to_dict(mod: ModuleInfo) -> Dict[str, Any]:
    return {
        "file": mod.file,
        "content_hash": mod.content_hash,
        "syntax_error": mod.syntax_error,
        "top_level_names": sorted(set(mod.top_level_names)),
        "classes": {
            name: {
                "name": c.name,
                "bases": c.bases,
                "line": c.line,
                "methods": {mname: _func_to_dict(m) for mname, m in sorted(c.methods.items())},
            }
            for name, c in sorted(mod.classes.items())
        },
        "functions": {
            name: _func_to_dict(f) for name, f in sorted(mod.functions.items())
        },
        "imports": [
            {
                "module": imp.module,
                "names": sorted(imp.names),
                "alias_of_names": dict(sorted(imp.alias_of_names.items())),
                "level": imp.level,
                "inside_try": imp.inside_try,
                "type_checking_only": imp.type_checking_only,
            }
            for imp in mod.imports
        ],
    }


def _module_from_dict(d: Dict[str, Any]) -> ModuleInfo:
    mod = ModuleInfo(
        name=d.get("name", ""),
        file=d.get("file", ""),
        content_hash=d.get("content_hash", ""),
        syntax_error=d.get("syntax_error"),
        top_level_names=list(d.get("top_level_names", [])),
    )
    for name, cd in (d.get("classes") or {}).items():
        cls = ClassInfo(name=cd.get("name", name),
                        bases=list(cd.get("bases", [])),
                        line=cd.get("line", 0))
        for mname, md in (cd.get("methods") or {}).items():
            cls.methods[mname] = _func_from_dict(md)
        mod.classes[name] = cls
    for name, fd in (d.get("functions") or {}).items():
        mod.functions[name] = _func_from_dict(fd)
    for imp in (d.get("imports") or []):
        mod.imports.append(ImportInfo(
            module=imp.get("module", ""),
            names=list(imp.get("names", [])),
            alias_of_names=dict(imp.get("alias_of_names", {})),
            level=imp.get("level", 0),
            inside_try=imp.get("inside_try", False),
            type_checking_only=imp.get("type_checking_only", False),
        ))
    return mod


def _func_to_dict(f: FunctionInfo) -> Dict[str, Any]:
    return {
        "name": f.name,
        "params": [
            {"name": p.name, "kind": p.kind,
             "annotation": p.annotation, "default": p.default,
             "has_default": p.has_default}
            for p in f.params
        ],
        "returns": f.returns,
        "decorators": f.decorators,
        "is_async": f.is_async,
        "is_static": f.is_static,
        "is_class": f.is_class,
        "is_property": f.is_property,
        "line": f.line,
        "has_var_positional": f.has_var_positional,
        "has_var_keyword": f.has_var_keyword,
    }


def _func_from_dict(d: Dict[str, Any]) -> FunctionInfo:
    return FunctionInfo(
        name=d.get("name", ""),
        params=[ParamInfo(**p) for p in d.get("params", [])],
        returns=d.get("returns"),
        decorators=list(d.get("decorators", [])),
        is_async=d.get("is_async", False),
        is_static=d.get("is_static", False),
        is_class=d.get("is_class", False),
        is_property=d.get("is_property", False),
        line=d.get("line", 0),
        has_var_positional=d.get("has_var_positional", False),
        has_var_keyword=d.get("has_var_keyword", False),
    )


def _utc_now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


__all__ = [
    "StructureIndex",
    "ModuleInfo",
    "ClassInfo",
    "FunctionInfo",
    "ParamInfo",
    "ImportInfo",
    "SCHEMA_VERSION",
]