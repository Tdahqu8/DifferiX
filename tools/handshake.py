"""
tools/handshake.py
-------------------
Assembles a deterministic, budget-aware session context for agents.

Sections, in priority order (highest to lowest):
  1. Task brief      — never trimmed
  2. Active plan     — never trimmed below the current subtask
  3. Structure       — public signatures of relevant modules only
  4. ADR             — recent session decisions
  5. Lessons         — A-Score ranked, role-scoped

Determinism contract:
  - Given identical inputs (state, index, ADR, lessons), the rendered
    string is byte-identical. This preserves provider-side prompt caching.
  - This function is PURE. It never records trigger counters, never
    writes to the memory database, never mutates state. If a caller wants
    to record that the retrieved lessons were actually injected into a
    prompt, they must call `tools.lesson_manager.record_lesson_trigger()`
    separately, using `result.lesson_node_ids`.

Public API:
  - SessionContextResult  (dataclass)
  - build_session_context(project_root, state, role, token_budget=None)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from tools.session_adr import SessionADR
from tools.structure_index import StructureIndex, ModuleInfo

logger = logging.getLogger(__name__)

DEFAULT_BUDGETS: Dict[str, int] = {
    "researcher": 8000,
    "editor": 6000,       # TCA shares this with T2/T3 now — keep smaller
    "reviewer": 6000,
    "default": 5000,
}

_SHARES = {
    "brief": 0.05,
    "plan": 0.20,
    "structure": 0.25,
    "adr": 0.15,
    "lessons": 0.35,
}

_CHARS_PER_TOKEN = 4
_SAFETY_MARGIN = 0.9


@dataclass
class SessionContextResult:
    """Output of build_session_context. Deterministic given identical inputs."""

    text: str = ""
    lesson_node_ids: List[str] = field(default_factory=list)
    sections_present: List[str] = field(default_factory=list)
    estimated_tokens: int = 0
    role: str = "researcher"

    def __bool__(self) -> bool:
        return bool(self.text)


def build_session_context(
    project_root: str,
    state: dict,
    role: str = "researcher",
    token_budget: Optional[int] = None,
    include_structure: bool = True,
    include_lessons: bool = True,
    include_adr: bool = True,
) -> SessionContextResult:
    """
    Assemble the handshake context. Never raises.

    On any internal error, degrades gracefully — a missing section is
    silently dropped rather than crashing the caller.
    """
    budget = token_budget or DEFAULT_BUDGETS.get(role, DEFAULT_BUDGETS["default"])
    char_budget = int(budget * _CHARS_PER_TOKEN * _SAFETY_MARGIN)

    parts: List[str] = []
    sections: List[str] = []

    brief = _render_brief(state)
    if brief:
        parts.append(brief)
        sections.append("brief")

    plan = _render_plan(state, char_limit=int(char_budget * _SHARES["plan"]))
    if plan:
        parts.append(plan)
        sections.append("plan")

    if include_structure:
        structure = _render_structure(
            project_root, state, char_limit=int(char_budget * _SHARES["structure"])
        )
        if structure:
            parts.append(structure)
            sections.append("structure")

    if include_adr:
        adr = _render_adr(
            project_root, token_limit=int(budget * _SHARES["adr"])
        )
        if adr:
            parts.append(adr)
            sections.append("adr")

    lesson_ids: List[str] = []
    if include_lessons:
        lessons, lesson_ids = _render_lessons(
            state, role, char_limit=int(char_budget * _SHARES["lessons"])
        )
        if lessons:
            parts.append(lessons)
            sections.append("lessons")

    total = sum(len(p) + 2 for p in parts)
    while total > char_budget and len(parts) > 2:
        parts.pop()
        sections.pop()
        total = sum(len(p) + 2 for p in parts)

    text = "\n\n".join(parts)
    return SessionContextResult(
        text=text,
        lesson_node_ids=lesson_ids,
        sections_present=sections,
        estimated_tokens=len(text) // _CHARS_PER_TOKEN,
        role=role,
    )


# ---------------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------------
def _render_brief(state: dict) -> str:
    goal = (
        state.get("goal")
        or state.get("task_prompt")
        or (state.get("task_spec") or {}).get("raw_user_prompt", "")
        or ""
    )
    subtask = state.get("active_subtask") or ""
    target = state.get("target_file") or ""

    if not goal and not subtask:
        return ""

    lines = ["## TASK BRIEF"]
    if goal:
        lines.append(f"Goal: {_clip(str(goal), 400)}")
    if subtask:
        lines.append(f"Current subtask: {_clip(str(subtask), 200)}")
    if target:
        lines.append(f"Target file: {target}")
    return "\n".join(lines)


def _render_plan(state: dict, char_limit: int) -> str:
    plan = state.get("plan", []) or []
    if not isinstance(plan, list) or not plan:
        return ""

    lines = ["## ACTIVE PLAN"]
    total = len(lines[0]) + 1
    for step in plan:
        if not isinstance(step, dict):
            continue
        status = str(step.get("status", "pending")).upper()
        title = str(step.get("title", "")).strip()
        if not title:
            continue
        entry = f"- [{status}] {_clip(title, 160)}"
        if total + len(entry) + 1 > char_limit:
            lines.append("  ... [additional steps truncated]")
            break
        lines.append(entry)
        total += len(entry) + 1
    return "\n".join(lines) if len(lines) > 1 else ""


def _render_structure(project_root: str, state: dict, char_limit: int) -> str:
    try:
        idx = StructureIndex.rebuild(project_root)
    except Exception as exc:
        logger.warning("Structure rebuild failed in handshake: %s", exc)
        return ""

    relevant = _select_relevant_modules(idx, state)
    if not relevant:
        return ""

    lines = ["## RELEVANT MODULE SIGNATURES"]
    total = len(lines[0]) + 1

    for mod_name in sorted(relevant):
        mod = idx.modules.get(mod_name)
        if mod is None or mod.syntax_error:
            continue
        block = _render_module_block(mod)
        if total + len(block) + 1 > char_limit:
            lines.append("... [additional modules truncated]")
            break
        lines.append(block)
        total += len(block) + 1
    return "\n".join(lines) if len(lines) > 1 else ""


def _select_relevant_modules(idx: StructureIndex, state: dict) -> List[str]:
    seeds: set = set()

    target = state.get("target_file")
    if target:
        mod = _module_for_path(str(target))
        if mod:
            seeds.add(mod)

    plan = state.get("plan", []) or []
    for step in plan:
        if isinstance(step, dict):
            tf = step.get("target_file")
            if tf:
                mod = _module_for_path(str(tf))
                if mod:
                    seeds.add(mod)

    expanded: set = set(seeds)
    for mod_name in seeds:
        mod = idx.modules.get(mod_name)
        if mod is None:
            continue
        for imp in mod.imports:
            dep = imp.module
            if dep and dep in idx.modules:
                expanded.add(dep)

    return list(expanded)


def _render_module_block(mod: ModuleInfo) -> str:
    lines = [f"### {mod.name}  ({mod.file})"]
    if mod.classes:
        for cname in sorted(mod.classes):
            cls = mod.classes[cname]
            bases = f"({', '.join(cls.bases)})" if cls.bases else ""
            lines.append(f"  class {cname}{bases}:")
            for mname in sorted(cls.methods):
                m = cls.methods[mname]
                if mname.startswith("_") and mname not in (
                    "__init__", "__enter__", "__exit__"
                ):
                    continue
                lines.append(f"    {_format_func(m)}")
    if mod.functions:
        for fname in sorted(mod.functions):
            f = mod.functions[fname]
            if fname.startswith("_"):
                continue
            lines.append(f"  {_format_func(f)}")
    return "\n".join(lines)


def _format_func(f) -> str:
    prefix = "async " if f.is_async else ""
    params = ", ".join(p.name for p in f.params if p.name != "self")
    ret = f" -> {f.returns}" if f.returns else ""
    return f"{prefix}{f.name}({params}){ret}"


def _render_adr(project_root: str, token_limit: int) -> str:
    try:
        adr = SessionADR(project_root)
        return adr.get_recent_summary(limit=5, token_budget=token_limit)
    except Exception as exc:
        logger.warning("ADR read failed in handshake: %s", exc)
        return ""


def _render_lessons(state: dict, role: str, char_limit: int):
    """
    Role-scoped A-Score lesson retrieval.

    Returns (rendered_text, node_ids). On failure returns ("", []).

    IMPORTANT: This function MUST be side-effect-free. It passes
    record_trigger=False so two consecutive calls with identical inputs
    return byte-identical output (prompt-caching determinism).
    """
    try:
        from tools.lesson_manager import get_ranked_lessons_for_role
    except Exception:
        return "", []

    query = " ".join(filter(None, [
        str(state.get("active_subtask") or ""),
        str(state.get("research_analysis") or ""),
        str(state.get("goal") or ""),
        str(state.get("target_file") or ""),
    ])).strip() or "general development"

    try:
        _scored, ctx, ids = get_ranked_lessons_for_role(
            role=role,
            query=query,
            limit=5,
            record_trigger=False,
        )
    except Exception as exc:
        logger.warning("Lesson retrieval failed in handshake: %s", exc)
        return "", []

    if not ctx:
        return "", []

    if len(ctx) > char_limit:
        ctx = ctx[:char_limit] + "\n...[lessons truncated]"

    return ctx, list(ids or [])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _module_for_path(path: str) -> Optional[str]:
    p = path.replace("\\", "/")
    if not p.endswith(".py"):
        return None
    if p.endswith("/__init__.py"):
        p = p[: -len("/__init__.py")]
    elif p.endswith(".py"):
        p = p[:-3]
    return p.replace("/", ".")


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


__all__ = ["build_session_context", "SessionContextResult", "DEFAULT_BUDGETS"]