"""
tools/context_assembler.py
--------------------------
Tiered Context Assembly (TCA) for LLM agents.

Problem
-------
Agents used to receive context assembled greedily: each component
(handshake, related files, lessons, iteration logs) added its own
contribution without a coordinating budget. This routinely produced
40,000+ token prompts of which 95% was noise, driving per-call cost up
by 3-5x.

Solution
--------
A single assembler with a fixed token budget per role. Content is
grouped into six tiers of decreasing priority. The assembler packs
tiers in order (T0 -> T5) until the budget is exhausted, then stops.
Content that doesn't fit is either downgraded to its fallback
representation or dropped.

Tiers
-----
  T0_STATIC       — system prompt, skills, response format. ALWAYS kept.
                    This is the cacheable prefix for provider-side caching.
  T1_ACTION_CORE  — goal, target file, task spec, session brief. Never
                    dropped silently (logged as WARNING if it doesn't fit).
  T2_TARGET_SLICE — the file being edited (possibly sliced) or the diff.
  T3_INTERFACE    — related files (signatures), session structure.
  T4_ENRICHMENT   — A-Score ranked lessons, ADR summary.
  T5_FEEDBACK     — past compile errors, rejections. Only on retries.

Determinism
-----------
Given identical input (same items, same order, same budget) the output
is byte-identical. This preserves prompt caching across calls.

Public API
----------
  Tier, TierItem, ContextBundle, AssembledContext, ContextAssembler
  estimate_tokens(text)
  smart_slice_code(code, target_file, max_lines)
  dedupe_logs(logs, max_errors)
  render_code_diff(old_code, new_code, target_file)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Dict, List

logger = logging.getLogger(__name__)

_CHARS_PER_TOKEN = 4


class Tier(IntEnum):
    T0_STATIC = 0
    T1_ACTION_CORE = 1
    T2_TARGET_SLICE = 2
    T3_INTERFACE = 3
    T4_ENRICHMENT = 4
    T5_FEEDBACK = 5


_TIER_LABELS = {
    Tier.T0_STATIC: "T0_static",
    Tier.T1_ACTION_CORE: "T1_action_core",
    Tier.T2_TARGET_SLICE: "T2_target_slice",
    Tier.T3_INTERFACE: "T3_interface",
    Tier.T4_ENRICHMENT: "T4_enrichment",
    Tier.T5_FEEDBACK: "T5_feedback",
}


@dataclass
class TierItem:
    """One piece of context destined for a specific tier."""
    tier: Tier
    key: str
    content: str
    fallback_content: str = ""
    optional: bool = False


@dataclass
class ContextBundle:
    """Ordered collection of items, grouped by tier at assemble-time."""
    items: List[TierItem] = field(default_factory=list)

    def add(self, item: TierItem) -> None:
        if item.content and item.content.strip():
            self.items.append(item)

    def add_text(self, tier: Tier, key: str, content: str, **kwargs) -> None:
        self.add(TierItem(tier=tier, key=key, content=content, **kwargs))

    def get_tier_items(self, tier: Tier) -> List[TierItem]:
        return [it for it in self.items if it.tier == tier]

    def render_tier(self, tier: Tier) -> str:
        items = self.get_tier_items(tier)
        if not items:
            return ""
        return "\n\n".join(it.content for it in items)


@dataclass
class AssembledContext:
    system_prompt: str = ""
    user_prompt: str = ""
    tokens_by_tier: Dict[str, int] = field(default_factory=dict)
    tokens_total: int = 0
    tokens_budget: int = 0
    degraded_tiers: List[str] = field(default_factory=list)
    dropped_items: List[str] = field(default_factory=list)
    cacheable_prefix_len: int = 0

    def log_summary(self) -> str:
        parts = [f"[TCA] budget={self.tokens_budget} used={self.tokens_total}"]
        for label, tok in self.tokens_by_tier.items():
            if tok:
                parts.append(f"{label}={tok}")
        if self.degraded_tiers:
            parts.append(f"degraded={','.join(self.degraded_tiers)}")
        if self.dropped_items:
            parts.append(f"dropped={len(self.dropped_items)}")
        return " ".join(parts)


def estimate_tokens(text: str) -> int:
    """Rough estimate: ~4 chars per token for English/code."""
    if not text:
        return 0
    return max(1, len(text) // _CHARS_PER_TOKEN)


class ContextAssembler:
    """
    Packs a ContextBundle into a bounded prompt.

    Usage:
        bundle = ContextBundle()
        bundle.add_text(Tier.T0_STATIC, "skills", skill_prompt)
        bundle.add_text(Tier.T1_ACTION_CORE, "spec", spec)
        ...
        assembled = ContextAssembler(budget=15000).assemble(bundle)
        # assembled.system_prompt, assembled.user_prompt
    """

    def __init__(self, budget: int, safety_margin: float = 0.9):
        self.budget = max(1000, int(budget))
        self.safety_margin = float(safety_margin)
        self._effective_budget = int(self.budget * self.safety_margin)

    def assemble(self, bundle: ContextBundle) -> AssembledContext:
        result = AssembledContext(
            tokens_budget=self.budget,
            tokens_by_tier={label: 0 for label in _TIER_LABELS.values()},
        )

        # ---- T0: always in full, this is the cacheable prefix ----
        t0 = bundle.render_tier(Tier.T0_STATIC)
        t0_tokens = estimate_tokens(t0)
        result.system_prompt = t0
        result.tokens_by_tier[_TIER_LABELS[Tier.T0_STATIC]] = t0_tokens

        remaining = self._effective_budget - t0_tokens
        if remaining < 0:
            logger.warning(
                "TCA: T0 (%d) exceeds budget (%d); truncating.",
                t0_tokens, self._effective_budget,
            )
            result.system_prompt = _truncate_to_tokens(t0, self._effective_budget)
            result.tokens_by_tier[_TIER_LABELS[Tier.T0_STATIC]] = self._effective_budget
            result.degraded_tiers.append("T0_static")
            result.tokens_total = self._effective_budget
            return result

        # ---- T1..T5: waterfall ----
        user_parts: List[str] = []

        for tier in (
            Tier.T1_ACTION_CORE,
            Tier.T2_TARGET_SLICE,
            Tier.T3_INTERFACE,
            Tier.T4_ENRICHMENT,
            Tier.T5_FEEDBACK,
        ):
            tier_items = bundle.get_tier_items(tier)
            if not tier_items:
                continue

            label = _TIER_LABELS[tier]
            used = 0

            for item in tier_items:
                item_tokens = estimate_tokens(item.content)

                if item_tokens <= remaining:
                    user_parts.append(item.content)
                    remaining -= item_tokens
                    used += item_tokens
                    continue

                # Doesn't fit — try fallback
                if item.fallback_content:
                    fb_tokens = estimate_tokens(item.fallback_content)
                    if fb_tokens <= remaining:
                        user_parts.append(item.fallback_content)
                        remaining -= fb_tokens
                        used += fb_tokens
                        result.degraded_tiers.append(f"{label}:{item.key}")
                        continue

                # Drop
                result.dropped_items.append(f"{label}:{item.key}")
                if not item.optional:
                    logger.warning(
                        "TCA: dropped non-optional %s:%s (%d tokens, %d remaining)",
                        label, item.key, item_tokens, remaining,
                    )

            result.tokens_by_tier[label] = used

        result.user_prompt = "\n\n".join(user_parts)
        result.tokens_total = sum(result.tokens_by_tier.values())
        result.cacheable_prefix_len = len(result.system_prompt)
        return result


def _truncate_to_tokens(text: str, max_tokens: int) -> str:
    max_chars = max_tokens * _CHARS_PER_TOKEN
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 30] + "\n...[truncated by TCA]"

# ---------------------------------------------------------------------------
# AST-aware symbol slicing for Python
# ---------------------------------------------------------------------------
import ast as _ast

_STOPWORD_HINTS = {
    "the", "and", "for", "with", "from", "into", "have", "will", "this",
    "that", "then", "than", "when", "where", "what", "which", "while",
    "there", "their", "them", "they", "make", "made", "with", "would",
    "could", "should", "must", "been", "were", "does", "done", "into",
    "also", "only", "just", "same", "each", "such", "some", "many",
}


def _extract_identifiers_from_text(text: str) -> set:
    """Pull identifier-like tokens from a hint string, drop stopwords."""
    if not text:
        return set()
    tokens = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]{3,}", text)
    return {t for t in tokens if t.lower() not in _STOPWORD_HINTS}


def _ast_symbols_with_ranges(code: str) -> dict:
    """
    Parse Python source, return {symbol_name: (start_line, end_line, kind)}.
    Only top-level symbols. kind in {'function', 'class', 'assignment'}.
    Lines are 1-indexed inclusive.
    """
    try:
        tree = _ast.parse(code)
    except SyntaxError:
        return {}

    symbols: dict = {}
    for node in tree.body:
        end_line = getattr(node, "end_lineno", None) or getattr(node, "lineno", 1)

        if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
            symbols[node.name] = (node.lineno, end_line, "function")
        elif isinstance(node, _ast.ClassDef):
            symbols[node.name] = (node.lineno, end_line, "class")
        elif isinstance(node, _ast.Assign):
            for target in node.targets:
                if isinstance(target, _ast.Name):
                    symbols[target.id] = (node.lineno, end_line, "assignment")
        elif isinstance(node, _ast.AnnAssign) and isinstance(node.target, _ast.Name):
            symbols[node.target.id] = (node.lineno, end_line, "assignment")
    return symbols


def _ast_imports_end_line(code: str) -> int:
    """Return the line number AFTER the last top-level import (or docstring)."""
    try:
        tree = _ast.parse(code)
    except SyntaxError:
        return 1

    last_import_end = 0
    for node in tree.body:
        if isinstance(node, (_ast.Import, _ast.ImportFrom)):
            last_import_end = max(last_import_end, getattr(node, "end_lineno", node.lineno))
        elif isinstance(node, _ast.Expr) and isinstance(node.value, _ast.Constant) and isinstance(node.value.value, str):
            # Module docstring — counts as part of the header
            last_import_end = max(last_import_end, getattr(node, "end_lineno", node.lineno))
        elif isinstance(node, _ast.Assign):
            # Allow `__all__ = [...]` after imports
            is_all = any(
                isinstance(t, _ast.Name) and t.id == "__all__"
                for t in node.targets
            )
            if is_all:
                last_import_end = max(last_import_end, getattr(node, "end_lineno", node.lineno))
            else:
                break
        else:
            break
    return last_import_end + 1


def _render_target_symbol_slice(
    lines: list,
    symbols: dict,
    target_name: str,
    imports_end: int,
    max_lines: int,
) -> str:
    """Imports + interface signatures + full target symbol body."""
    total = len(lines)
    t_start, t_end, t_kind = symbols[target_name]
    t_budget = t_end - t_start + 1

    out = []
    out.append(f"# ============ FILE (symbol slice) ============")
    out.append(f"# Total lines: {total}")
    out.append(f"# Target: {t_kind} `{target_name}` (lines {t_start}-{t_end})")
    out.append(f"# Full file is on disk. Slices below are for reference only.")
    out.append(f"# Use SEARCH/REPLACE blocks. Anchor MUST be copied verbatim from this view.")
    out.append("")

    # Imports (never truncated below 30 lines)
    imports_slice = min(imports_end, max(15, max_lines // 6))
    out.append(f"# --- IMPORTS (lines 1-{imports_slice}) ---")
    out.extend(lines[:imports_slice])
    if imports_end > imports_slice:
        out.append(f"# ... [{imports_end - imports_slice} more import lines] ...")
    out.append("")

    # Interface signatures (excluding target)
    out.append("# --- PUBLIC INTERFACE (signatures, for coherence) ---")
    shown_interface = 0
    for name, (s, e, k) in symbols.items():
        if name == target_name:
            continue
        if shown_interface >= 30:
            out.append(f"# ... [{len(symbols) - 1 - shown_interface} more symbols] ...")
            break
        first_line = lines[s - 1].rstrip()
        out.append(f"{first_line}  # L{s}-{e}")
        shown_interface += 1
    out.append("")

    # Context before (max 3 lines)
    before = 3
    if t_start > imports_end + 1:
        ctx_start = max(imports_end, t_start - before)
        omitted = t_start - ctx_start - 1
        if omitted > 0:
            out.append(f"# ... [{omitted} lines omitted] ...")
        if ctx_start < t_start:
            out.append(f"# --- LINES {ctx_start + 1}-{t_start - 1} (context before) ---")
            out.extend(lines[ctx_start:t_start - 1])
            out.append("")

    # Target body
    out.append(f"# --- TARGET SYMBOL: {target_name} (lines {t_start}-{t_end}) ---")
    # If the target alone exceeds the budget, truncate the body
    already_used = len(out)
    remaining_lines = max_lines - already_used - 20  # reserve for tail
    if t_budget > remaining_lines > 30:
        head_n = remaining_lines - 15
        tail_n = 15
        out.extend(lines[t_start - 1:t_start - 1 + head_n])
        out.append(
            f"# ... [{t_budget - head_n - tail_n} lines omitted from "
            f"{target_name}; the full body exists on disk] ..."
        )
        out.extend(lines[t_end - tail_n:t_end])
    else:
        out.extend(lines[t_start - 1:t_end])
    out.append("")

    # Context after (max 3 lines)
    after = 3
    if t_end < total:
        ctx_end = min(total, t_end + after)
        out.append(f"# --- LINES {t_end + 1}-{ctx_end} (context after) ---")
        out.extend(lines[t_end:ctx_end])
        out.append("")
        remaining = total - ctx_end
        if remaining > 15:
            out.append(f"# ... [{remaining} more lines to end of file] ...")

    # Tail (last 10 lines) — matters for module-level registries / __all__
    if total > 15 and t_end < total - 10:
        out.append("")
        out.append(f"# --- END OF FILE (last 10 lines) ---")
        out.extend(lines[-10:])

    return "\n".join(out)


def _smart_slice_python(
    code: str,
    target_file: str,
    hints: str,
    max_lines: int,
) -> str:
    """AST-aware slicing for Python files with symbol-detection from hints."""
    lines = code.splitlines()
    if len(lines) <= max_lines:
        return code

    symbols = _ast_symbols_with_ranges(code)
    imports_end = _ast_imports_end_line(code)

    hint_tokens = _extract_identifiers_from_text(hints)
    # Prefer function/class over assignment; among same kind, prefer longest name
    # (longer names are more specific, less likely to be a coincidental match).
    target_name = max(
        (n for n in hint_tokens if n in symbols),
        key=lambda n: (symbols[n][2] in ("function", "class"), len(n)),
        default=None,
    )

    if target_name:
        return _render_target_symbol_slice(
            lines, symbols, target_name, imports_end, max_lines
        )

    # No symbol match — fall through to line-based slicing
    return _render_line_slice(code, target_file, max_lines)

# ---------------------------------------------------------------------------
# Code slicing for T2
# ---------------------------------------------------------------------------
_BACKEND_EXTS = {"py", "pyi", "rb", "go", "rs", "java", "kt", "c", "cpp", "h", "hpp"}
_FRONTEND_EXTS = {"html", "htm", "css", "scss"}


def _render_line_slice(code: str, target_file: str, max_lines: int = 150) -> str:
    """
    Slice a file to fit a token budget, keeping the parts most likely
    relevant for editing.

    - Frontend (HTML/CSS): keep first (max_lines - 30) + last 30 lines.
      New sections typically get appended near the closing tags.
    - Backend (Python/Rust/etc): keep first (max_lines - 15) + last 15.
    - If file fits in max_lines, return unchanged.

    Emits a prominent warning block instructing the model to use
    SEARCH/REPLACE and NOT do a full rewrite on a truncated view.
    """
    if not code:
        return code
    lines = code.splitlines()
    if len(lines) <= max_lines:
        return code

    ext = target_file.rsplit(".", 1)[-1].lower() if "." in target_file else ""

    if ext in _FRONTEND_EXTS:
        head_lines = max_lines - 30
        tail_lines = 30
        if ext in ("html", "htm"):
            marker = "<!--"
        else:
            marker = "/*"
    else:
        head_lines = max_lines - 15
        tail_lines = 15
        marker = "#" if ext in _BACKEND_EXTS else "//"

    omitted = len(lines) - head_lines - tail_lines
    if omitted <= 0:
        return code

    head = "\n".join(lines[:head_lines])
    tail = "\n".join(lines[-tail_lines:])
    last_visible = len(lines) - tail_lines

    warning = (
        f"\n{marker} ============ WARNING: FILE TRUNCATED ============\n"
        f"{marker} Lines 1-{head_lines} and {last_visible + 1}-{len(lines)} "
        f"of {len(lines)} shown. {omitted} lines omitted in the middle.\n"
        f"{marker}\n"
        f"{marker} RULES:\n"
        f"{marker} 1. Use SEARCH/REPLACE blocks ONLY. Do NOT output the full file.\n"
        f"{marker} 2. SEARCH anchors MUST come from the visible portions above.\n"
        f"{marker} 3. To edit the omitted middle section, anchor on the nearest\n"
        f"{marker}    visible line at the boundary of the gap.\n"
        f"{marker} =====================================================\n"
    )

    return f"{head}\n{warning}\n{tail}"

def smart_slice_code(
    code: str,
    target_file: str,
    max_lines: int = 150,
    hints: str = "",
) -> str:
    """
    Slice code for the editing prompt.

    For Python files with symbol hints, uses AST to isolate the target
    function/class. For everything else, falls back to head+tail line slicing.

    `hints` should contain text likely to mention the symbol being edited
    (e.g. the plan step title + researcher analysis).
    """
    if not code:
        return code
    lines = code.splitlines()
    if len(lines) <= max_lines:
        return code

    ext = target_file.rsplit(".", 1)[-1].lower() if "." in target_file else ""

    if ext in ("py", "pyi") and hints:
        return _smart_slice_python(code, target_file, hints, max_lines)

    return _render_line_slice(code, target_file, max_lines)


# ---------------------------------------------------------------------------
# Log deduplication for T5
# ---------------------------------------------------------------------------
_LINE_NUMBER_RE = re.compile(r":\d+:\d+|line \d+|Line \d+")


def dedupe_logs(logs: str, max_errors: int = 5, keep_tail_chars: int = 300) -> str:
    """
    Compress compiler/iteration logs by extracting unique error/warning
    lines, normalizing line numbers, and capping at max_errors.
    """
    if not logs or len(logs) < 200:
        return logs.strip()

    seen: set = set()
    picked: List[str] = []

    for line in logs.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        lower = stripped.lower()
        if not any(tok in lower for tok in (
            "error", "warning", "fail", "reject", "invalid", "exception"
        )):
            continue
        normalized = _LINE_NUMBER_RE.sub(":N", stripped)
        if normalized in seen:
            continue
        seen.add(normalized)
        picked.append(stripped)
        if len(picked) >= max_errors:
            break

    if not picked:
        tail = logs[-keep_tail_chars:]
        return f"[no distinct errors; tail]\n{tail}"

    header = f"[{len(picked)} distinct error/warning line(s), {len(logs)} chars in raw log]"
    return header + "\n" + "\n".join(picked)


# ---------------------------------------------------------------------------
# Diff rendering for reviewer T2
# ---------------------------------------------------------------------------
def render_code_diff(
    old_code: str,
    new_code: str,
    target_file: str,
    context_lines: int = 3,
) -> str:
    """
    Render a unified diff. Returns "" if old == new or diff computation
    fails (caller should fall back to sliced code).

    For small edits in large files, this is 10-30x smaller than the full
    code — the reviewer rarely needs the unchanged portions.
    """
    if not old_code or not new_code:
        return ""
    if old_code == new_code:
        return ""

    try:
        import difflib
        diff_lines = list(difflib.unified_diff(
            old_code.splitlines(),
            new_code.splitlines(),
            fromfile=f"a/{target_file}",
            tofile=f"b/{target_file}",
            lineterm="",
            n=context_lines,
        ))
    except Exception as exc:
        logger.debug("Diff rendering failed: %s", exc)
        return ""

    if not diff_lines:
        return ""

    return (
        f"=== DIFF (unified, context={context_lines}) ===\n"
        + "\n".join(diff_lines)
    )


__all__ = [
    "Tier",
    "TierItem",
    "ContextBundle",
    "AssembledContext",
    "ContextAssembler",
    "estimate_tokens",
    "smart_slice_code",
    "dedupe_logs",
    "render_code_diff",
]