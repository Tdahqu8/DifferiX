"""
tools/project_context_gatherer.py
----------------------------------
Assembles "related file" context for the editor.

When the editor is asked to write styles.css, it needs to know what classes
exist in index.html. When it's asked to write script.js, it needs to know
what selectors and DOM hooks exist in both. Without this context, the editor
invents its own naming and the resulting files silently diverge.

To save prompt tokens (which are expensive), we aggressively truncate files.
We only extract the top lines (imports, signatures, class definitions)
because that's all that's needed to understand the interface.

Public API:
  - gather_related_context(project_root, target_file, plan, budget_tokens=2000)
        -> RelatedContext
  - render_related_context(ctx) -> str
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# Relationship hints: target extension -> set of extensions to consider related.
_RELATED_BY_EXT: Dict[str, Set[str]] = {
    ".html": {".css", ".scss", ".js", ".mjs", ".ts", ".tsx"},
    ".htm":  {".css", ".scss", ".js", ".mjs", ".ts", ".tsx"},
    ".css":  {".html", ".htm"},
    ".scss": {".html", ".htm"},
    ".js":   {".html", ".htm", ".css", ".scss", ".jsx", ".tsx"},
    ".mjs":  {".html", ".htm", ".css", ".scss"},
    ".ts":   {".html", ".htm", ".css", ".scss", ".tsx"},
    ".tsx":  {".html", ".htm", ".css", ".scss", ".ts"},
    ".py":   {".py"},
    ".cs":   {".cs", ".csproj"},
}

# Files we never include as related context.
_BLOCKED_BASENAMES = {
    "state.json", "projects_registry.json", "skills_config.json",
    "active_models.json", "models_cache.json", "package-lock.json",
    "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "Pipfile.lock",
    ".env", ".env.local", ".env.production",
    "main.py",  # Huge file (2000+ lines). Don't feed it to the agent as context.
}

# Directories we never recurse into for related context.
_BLOCKED_DIRS = {
    ".git", ".differix", "node_modules", "__pycache__", ".venv", "venv",
    "dist", "build", ".pytest_cache", ".mypy_cache", "iteration_logs",
    ".next", ".nuxt", ".cache", "coverage",
}

# How much of each related file to include. We cap total at the budget.
_MAX_RELATED_FILES = 4
_MAX_LINES_PER_FILE = 120


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------
@dataclass
class RelatedFile:
    rel_path: str
    content: str  # possibly truncated
    original_length: int
    truncated: bool
    reason: str  # "explicit-plan" | "same-dir" | "extension-match" | "import"


@dataclass
class RelatedContext:
    target_file: str
    files: List[RelatedFile] = field(default_factory=list)
    total_chars: int = 0
    notes: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# File scoring / selection
# ---------------------------------------------------------------------------
def _is_blocked(rel_path: str) -> bool:
    base = os.path.basename(rel_path)
    if base in _BLOCKED_BASENAMES:
        return True
    parts = rel_path.replace("\\", "/").split("/")
    return any(p in _BLOCKED_DIRS or p.startswith(".") for p in parts[:-1])


def _read_file(path: str, max_bytes: int = 200_000) -> Optional[str]:
    try:
        size = os.path.getsize(path)
        if size > max_bytes:
            return None
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def _truncate_content(content: str, char_budget: int, rel_path: str) -> tuple:
    """
    Truncate by lines. We only need the top of the file (imports, 
    class defs, signatures) to understand the structure.
    """
    lines = content.splitlines()
    
    if len(lines) > _MAX_LINES_PER_FILE:
        head = "\n".join(lines[:_MAX_LINES_PER_FILE])
        omitted = len(lines) - _MAX_LINES_PER_FILE
        return head + f"\n\n# ... [{omitted} lines omitted for brevity] ...", True
        
    # If the file is small in lines but huge in chars (e.g. minified JS/CSS)
    if len(content) > char_budget:
        return content[:char_budget] + "\n# ... [truncated by chars] ...", True
        
    return content, False


def _score_related(
    candidate_rel: str,
    target_rel: str,
    target_dir: str,
    plan_targets: Set[str],
    target_ext: str,
) -> Optional[tuple]:
    """
    Return (score, reason) if the candidate is related, else None.
    Higher score = more related.
    """
    if candidate_rel == target_rel:
        return None
    if _is_blocked(candidate_rel):
        return None

    cand_ext = os.path.splitext(candidate_rel)[1].lower()
    cand_dir = os.path.dirname(candidate_rel)

    related_exts = _RELATED_BY_EXT.get(target_ext, set())
    if cand_ext not in related_exts:
        return None

    # Same basename, different extension: index.html ↔ index.css
    target_base = os.path.splitext(os.path.basename(target_rel))[0]
    cand_base = os.path.splitext(os.path.basename(candidate_rel))[0]

    if candidate_rel in plan_targets:
        return (100, "explicit-plan")
    if cand_base == target_base:
        return (80, "same-basename")
    if cand_dir == target_dir:
        return (50, "same-dir")
    return (10, "extension-match")


def _plan_targets(plan: Optional[list]) -> Set[str]:
    targets: Set[str] = set()
    if not isinstance(plan, list):
        return targets
    for step in plan:
        if isinstance(step, dict):
            tf = step.get("target_file")
            if isinstance(tf, str) and tf:
                targets.add(tf.replace("\\", "/"))
    return targets


# ---------------------------------------------------------------------------
# Discovery helpers
# ---------------------------------------------------------------------------
def _walk_candidates(project_root: str, max_files: int = 2000) -> List[str]:
    """Return project-relative paths of potential related files, capped."""
    out: List[str] = []
    root = Path(project_root).resolve()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames
            if d not in _BLOCKED_DIRS and not d.startswith(".")
        ]
        for fname in filenames:
            lower = fname.lower()
            if not lower.endswith((
                ".html", ".htm", ".css", ".scss", ".js", ".mjs", ".ts",
                ".tsx", ".jsx", ".py", ".cs",
            )):
                continue
            rel = os.path.relpath(os.path.join(dirpath, fname), root).replace("\\", "/")
            if _is_blocked(rel):
                continue
            out.append(rel)
            if len(out) >= max_files:
                return out
    return out

# ---------------------------------------------------------------------------
# HTML class inventory (for CSS editing)
# ---------------------------------------------------------------------------
_HTML_CLASS_ATTR_RE_DQ = re.compile(r'class\s*=\s*"([^"]*)"', re.IGNORECASE)
_HTML_CLASS_ATTR_RE_SQ = re.compile(r"class\s*=\s*'([^']*)'", re.IGNORECASE)
_VALID_CLASS_TOKEN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_-]*$")


def extract_html_class_inventory(
    html: str,
    rel_path: str = "html",
    grouped_threshold: int = 2,
) -> str:
    """
    Render only the class names from an HTML file, grouped by prefix.

    When the editor is writing CSS, it does not need the full HTML body —
    only the class names it must style. This renders a ~200-token inventory
    instead of a ~2000-token HTML file.

    Example output:
        ### CLASS INVENTORY (from index.html) — for CSS coherence ###
        Use these class names verbatim in your CSS rules. Do NOT rename them.

          .hero-title, .hero-subtitle, .hero-actions
          .feature-card, .feature-icon
          Ungrouped: .container, .btn

        [12 total classes in index.html]
    """
    if not html or not html.strip():
        return ""

    classes: Set[str] = set()
    for pattern in (_HTML_CLASS_ATTR_RE_DQ, _HTML_CLASS_ATTR_RE_SQ):
        for m in pattern.finditer(html):
            for token in m.group(1).split():
                token = token.strip()
                if token and _VALID_CLASS_TOKEN.match(token):
                    classes.add(token)

    if not classes:
        return ""

    groups: Dict[str, List[str]] = {}
    for cls in classes:
        prefix = cls.split("-", 1)[0] if "-" in cls else ""
        groups.setdefault(prefix, []).append(cls)

    grouped_rows: List[str] = []
    ungrouped: List[str] = []
    for prefix in sorted(groups):
        members = sorted(groups[prefix])
        if prefix and len(members) >= grouped_threshold:
            grouped_rows.append("  " + ", ".join("." + m for m in members))
        else:
            ungrouped.extend(members)

    lines = [f"### CLASS INVENTORY (from {rel_path}) — for CSS coherence ###"]
    lines.append(
        "Use these class names verbatim in your CSS rules. Do NOT rename them."
    )
    lines.append("")
    if grouped_rows:
        lines.extend(grouped_rows)
    if ungrouped:
        ungrouped.sort()
        lines.append("  Ungrouped: " + ", ".join("." + u for u in ungrouped))
    lines.append("")
    lines.append(f"[{len(classes)} total classes in {rel_path}]")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def gather_related_context(
    project_root: str,
    target_file: str,
    plan: Optional[list] = None,
    budget_tokens: int = 2000,  # Reduced from 3000
    max_files: int = _MAX_RELATED_FILES,
) -> RelatedContext:
    """
    Assemble related files for the editor.
    """
    target_rel = target_file.replace("\\", "/")
    target_ext = os.path.splitext(target_rel)[1].lower()
    target_dir = os.path.dirname(target_rel)

    ctx = RelatedContext(target_file=target_rel)

    if target_ext not in _RELATED_BY_EXT:
        ctx.notes.append(f"no related-file rules for extension {target_ext!r}")
        return ctx

    plan_targets = _plan_targets(plan)
    candidates = _walk_candidates(project_root)

    scored: List[tuple] = []
    for cand in candidates:
        result = _score_related(cand, target_rel, target_dir, plan_targets, target_ext)
        if result is not None:
            score, reason = result
            scored.append((score, cand, reason))

    if not scored:
        return ctx

    scored.sort(key=lambda t: (-t[0], t[1]))

    char_budget = int(budget_tokens * 4 * 0.9)   # 4 chars/token, 10% safety
    per_file_budget = max(500, char_budget // max(1, min(max_files, len(scored))))

    total = 0
    for score, cand, reason in scored:
        if len(ctx.files) >= max_files:
            break
        if total >= char_budget:
            break

        abs_path = os.path.join(project_root, cand)
        content = _read_file(abs_path)
        if content is None:
            continue
        if not content.strip():
            continue

        remaining = char_budget - total
        slice_budget = min(per_file_budget, remaining)
        if slice_budget < 200:
            break

        # CSS editing needs only class names from HTML, not the HTML body.
        # Emit a compact inventory (~200 tokens) instead of a truncated
        # 120-line slice (~2000 tokens).
        cand_ext = os.path.splitext(cand)[1].lower()
        if target_ext in (".css", ".scss") and cand_ext in (".html", ".htm"):
            inventory = extract_html_class_inventory(content, rel_path=cand)
            if inventory:
                ctx.files.append(RelatedFile(
                    rel_path=cand,
                    content=inventory,
                    original_length=len(content),
                    truncated=True,
                    reason=f"{reason} (class-inventory)",
                ))
                total += len(inventory)
                continue
            # Empty inventory — fall through to normal truncation.

        truncated_content, was_truncated = _truncate_content(
            content, slice_budget, cand
        )

        ctx.files.append(RelatedFile(
            rel_path=cand,
            content=truncated_content,
            original_length=len(content),
            truncated=was_truncated,
            reason=reason,
        ))
        total += len(truncated_content)

    ctx.total_chars = total
    return ctx


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def render_related_context(ctx: RelatedContext) -> str:
    """
    Render the RelatedContext for injection into the editor prompt.
    Empty string if nothing was found.
    """
    if not ctx.files:
        return ""

    lines = ["### RELATED FILES (context for coherence) ###"]
    lines.append(
        "The following files share the same project. Ensure class names, "
        "imports, and function names you introduce are consistent with them."
    )
    lines.append("")

    for rf in ctx.files:
        note = " (truncated)" if rf.truncated else ""
        lines.append(f"--- {rf.rel_path} ({rf.reason}){note} ---")
        lines.append(rf.content)
        lines.append("")

    return "\n".join(lines)


__all__ = [
    "RelatedContext",
    "RelatedFile",
    "gather_related_context",
    "render_related_context",
    "extract_html_class_inventory",
]