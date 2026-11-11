"""
tools/lesson_manager.py
-----------------------
File-backed lesson storage (source of truth) + A-Score retrieval bridge.

All existing file APIs are preserved unchanged. New APIs expose ranked
retrieval and the feedback loop via LessonMemoryBridge.

Trigger semantics:
  - Retrieval functions default to `record_trigger=True` (backward-compatible).
  - Pure context-builders (handshake) MUST pass `record_trigger=False`.
  - Callers who build an actual LLM prompt SHOULD call `record_lesson_trigger()`
    explicitly, once, with the lesson node IDs that ended up in the prompt.
"""

from __future__ import annotations

import glob
import json
import os
import uuid
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Sequence

import config

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

LESSONS_DIRS: Dict[str, str] = {
    "observations": os.path.join(BASE_DIR, "lessons", "observations"),
    "candidates": os.path.join(BASE_DIR, "lessons", "candidates"),
    "promoted": os.path.join(BASE_DIR, "lessons", "promoted"),
    "deprecated": os.path.join(BASE_DIR, "lessons", "deprecated"),
}


# ---------------------------------------------------------------------------
# File layer
# ---------------------------------------------------------------------------
def init_lessons_dirs() -> None:
    for path in LESSONS_DIRS.values():
        os.makedirs(path, exist_ok=True)


init_lessons_dirs()


def is_duplicate_observation(
    new_root_cause: str, new_details: dict, threshold: float = 0.85
) -> bool:
    obs_dir = LESSONS_DIRS["observations"]
    if not os.path.exists(obs_dir):
        return False

    new_rule_str = json.dumps(new_details or {}, sort_keys=True).lower()
    new_cause_str = str(new_root_cause).lower()

    for file_path in glob.glob(os.path.join(obs_dir, "*.json")):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                existing = json.load(f)
            existing_cause = str(existing.get("root_cause", "")).lower()
            existing_rule_str = json.dumps(
                existing.get("details", {}), sort_keys=True
            ).lower()
            cause_sim = SequenceMatcher(None, new_cause_str, existing_cause).ratio()
            rule_sim = SequenceMatcher(None, new_rule_str, existing_rule_str).ratio()
            if cause_sim > threshold or rule_sim > threshold:
                return True
        except Exception:
            continue
    return False


def create_observation(root_cause: str, details: dict = None) -> Optional[str]:
    if is_duplicate_observation(root_cause, details):
        print(f"[Lesson Manager] Skipped duplicate observation: '{root_cause}'")
        return None

    init_lessons_dirs()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    unique_id = uuid.uuid4().hex[:6]
    filename = f"observation_{timestamp}_{unique_id}.json"
    filepath = os.path.join(LESSONS_DIRS["observations"], filename)

    data = {
        "id": unique_id,
        "timestamp": datetime.now().isoformat(),
        "root_cause": root_cause,
        "details": details or {},
    }

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    _invalidate_bridge(reason=f"observation created: {filename}")
    return filename


def move_lesson(filename: str, source_category: str, target_category: str) -> bool:
    init_lessons_dirs()
    if source_category not in LESSONS_DIRS or target_category not in LESSONS_DIRS:
        raise ValueError(f"Invalid category: {source_category} or {target_category}")

    src_path = os.path.join(LESSONS_DIRS[source_category], filename)
    dst_path = os.path.join(LESSONS_DIRS[target_category], filename)

    if not os.path.exists(src_path):
        return False

    if os.path.exists(dst_path):
        base, ext = os.path.splitext(filename)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dst_path = os.path.join(
            LESSONS_DIRS[target_category], f"{base}_{stamp}{ext}"
        )

    os.rename(src_path, dst_path)
    _invalidate_bridge(reason=f"lesson moved {source_category} → {target_category}")
    return True


def move_to_candidates(filename: str, source_category: str = "observations") -> bool:
    return move_lesson(filename, source_category, "candidates")


def move_to_promoted(filename: str, source_category: str = "candidates") -> bool:
    return move_lesson(filename, source_category, "promoted")


def move_to_deprecated(filename: str, source_category: str = "promoted") -> bool:
    return move_lesson(filename, source_category, "deprecated")


def load_lessons_from_category(category: str) -> List[dict]:
    init_lessons_dirs()
    if category not in LESSONS_DIRS:
        return []
    lessons = []
    target_dir = LESSONS_DIRS[category]
    for filename in sorted(os.listdir(target_dir)):
        if not filename.endswith(".json"):
            continue
        try:
            with open(os.path.join(target_dir, filename), "r", encoding="utf-8") as f:
                data = json.load(f)
                data["_filename"] = filename
                lessons.append(data)
        except Exception as e:
            print(f"[Warning] Failed to parse lesson file {filename}: {e}")
    return lessons


def load_promoted_lessons() -> List[dict]:
    return load_lessons_from_category("promoted")


# ---------------------------------------------------------------------------
# Legacy prompt formatting
# ---------------------------------------------------------------------------
def format_lessons_category_prompt(category_name: str, header_title: str) -> str:
    lessons = load_lessons_from_category(category_name)
    if not lessons:
        return f"No {category_name} items found."

    formatted = [f"### {header_title} ###"]
    for idx, item in enumerate(lessons, 1):
        filename = item.get("_filename", "unknown.json")
        cause = item.get("root_cause", "N/A")
        details = item.get("details", {})
        formatted.append(f"{idx}. [{filename}] Root Cause: {cause}")
        if isinstance(details, dict):
            for k, v in details.items():
                formatted.append(f"   - **{k}:** {v}")
        elif details:
            formatted.append(f"   - {details}")
    return "\n".join(formatted)


def get_promoted_lessons_prompt() -> str:
    return format_lessons_category_prompt(
        "promoted", "SYSTEM LESSONS & ARCHITECTURAL MEMORY (PROMOTED)"
    )


def get_observations_prompt() -> str:
    return format_lessons_category_prompt(
        "observations", "RAW UNPROCESSED OBSERVATIONS"
    )


def get_candidates_prompt() -> str:
    return format_lessons_category_prompt(
        "candidates", "LESSON CANDIDATES (PENDING PROMOTION)"
    )


def get_deprecated_prompt() -> str:
    return format_lessons_category_prompt(
        "deprecated", "DEPRECATED LESSONS (HISTORICAL)"
    )


def get_full_memory_prompt() -> str:
    return (
        f"{get_promoted_lessons_prompt()}\n\n"
        f"{get_candidates_prompt()}\n\n"
        f"{get_observations_prompt()}\n\n"
        f"{get_deprecated_prompt()}"
    )


def get_lessons_context_for_role(role_name: str) -> str:
    role = (role_name or "").strip().lower()
    if role in ("researcher", "superuser", "architect"):
        return get_full_memory_prompt()
    return get_promoted_lessons_prompt()


# ---------------------------------------------------------------------------
# Bridge integration
# ---------------------------------------------------------------------------
_bridge = None


def _invalidate_bridge(reason: str = "") -> None:
    global _bridge
    if _bridge is not None:
        _bridge._needs_sync = True


def _get_bridge():
    global _bridge
    if not getattr(config, "LESSONS_MEMORY_ENABLED", True):
        return None
    if _bridge is None:
        try:
            from tools.lesson_memory_bridge import LessonMemoryBridge
        except ImportError:
            try:
                from lesson_memory_bridge import LessonMemoryBridge
            except ImportError:
                print(
                    "[LessonManager] Bridge module not found — falling back to file mode."
                )
                return None

        try:
            _bridge = LessonMemoryBridge(
                lessons_root=config.LESSONS_ROOT,
                db_path=config.LESSONS_MEMORY_DB_PATH,
                embedding_provider=None,
            )
            _bridge._needs_sync = True
        except Exception as exc:
            print(f"[LessonManager] Bridge init failed ({exc}) — using file mode.")
            _bridge = None
            return None

    if getattr(_bridge, "_needs_sync", False):
        try:
            report = _bridge.sync_all()
            if report.errors:
                for err in report.errors:
                    print(f"[LessonManager] Sync warning: {err}")
            _bridge._needs_sync = False
        except Exception as exc:
            print(f"[LessonManager] Sync failed ({exc}) — using cached index.")

    return _bridge


# ---------------------------------------------------------------------------
# Ranked retrieval (with explicit record_trigger control)
# ---------------------------------------------------------------------------
def get_ranked_lessons_scored(
    query: str,
    limit: int = 5,
    stages: Optional[Sequence[str]] = None,
    scope_type=None,
    scope_value: str = "differix",
    record_trigger: bool = True,
):
    """
    A-Score rank lessons matching `query`, filtered by stage.

    `record_trigger=True` increments T (trigger_count) and refreshes
    last_used_at for every returned node. Callers that are *previewing*
    context (handshake, debug commands) MUST pass record_trigger=False
    to preserve determinism and avoid inflating T.
    """
    bridge = _get_bridge()
    if bridge is None:
        return []

    from memory import MemoryScope

    if stages is None:
        stages = getattr(
            config, "LESSONS_RETRIEVAL_STAGES_CONSUMER", ("promoted",)
        )

    try:
        return bridge.search(
            query=query,
            limit=limit,
            stages=stages,
            scope_type=scope_type or MemoryScope.PROJECT,
            scope_value=scope_value,
            record_trigger=record_trigger,
        )
    except Exception as exc:
        print(f"[LessonManager] Ranked search failed ({exc}).")
        return []


def get_ranked_lessons_context(
    query: str,
    limit: int = 5,
    stages: Optional[Sequence[str]] = None,
    scope_value: str = "differix",
    record_trigger: bool = True,
) -> str:
    """
    Same as get_ranked_lessons_scored, formatted for LLM injection.

    Falls back to the legacy prompt formatter if the bridge is unavailable.
    """
    scored = get_ranked_lessons_scored(
        query=query,
        limit=limit,
        stages=stages,
        scope_value=scope_value,
        record_trigger=record_trigger,
    )

    if not scored:
        if stages and "promoted" in stages and len(stages) == 1:
            return get_promoted_lessons_prompt()
        return get_full_memory_prompt()

    return format_scored_lessons_context(scored)


def format_scored_lessons_context(
    scored,
    header: str = "RELEVANT LESSONS (A-SCORE RANKED)",
) -> str:
    """
    Render a List[ScoredMemoryNode] into the LLM-injection format.

    Use this when you already have the scored list (e.g. so you can also
    persist node IDs to state) — avoids retrieving lessons twice per cycle.
    """
    if not scored:
        return ""
    lines: List[str] = [
        f"### {header} ###",
        "Format: IF {trigger} THEN avoid {rule} BECAUSE {root_cause}",
        "",
    ]
    for idx, s in enumerate(scored, 1):
        node = s.node
        md = node.metadata or {}
        stage = md.get("stage", "unknown")
        cause = md.get("root_cause", "N/A")
        details = md.get("details", {}) or {}
        rule = details.get("rule") or details.get("fix_instruction") or ""
        trigger = details.get("trigger_condition") or details.get("context") or ""

        lines.append(
            f"{idx}. [stage={stage} | score={s.score:.4f} | "
            f"sim={s.similarity:.3f} | T={node.trigger_count} "
            f"H={node.helpful_count} V={node.violation_count}]"
        )
        if trigger and rule:
            lines.append(f"   IF {trigger}")
            lines.append(f"   THEN avoid {rule}")
            lines.append(f"   BECAUSE {cause}")
        else:
            lines.append(f"   root_cause: {cause}")
            for k, v in details.items():
                lines.append(f"   - {k}: {v}")
        lines.append("")

    return "\n".join(lines)


def get_ranked_lessons_for_role(
    role: str,
    query: str,
    limit: int = 5,
    record_trigger: bool = True,
) -> tuple:
    """
    Role-aware ranked retrieval. Returns (scored_list, context_string, node_ids).

    RBAC (matches get_lessons_context_for_role):
      - researcher / superuser / architect → promoted + candidates + observations
      - anyone else → promoted only

    Pure context-builders (handshake) MUST pass record_trigger=False.
    """
    role_key = (role or "").strip().lower()
    if role_key in ("researcher", "superuser", "architect"):
        stages = getattr(
            config,
            "LESSONS_RETRIEVAL_STAGES_RESEARCHER",
            ("promoted", "candidates", "observations"),
        )
    else:
        stages = getattr(
            config, "LESSONS_RETRIEVAL_STAGES_CONSUMER", ("promoted",)
        )

    scored = get_ranked_lessons_scored(
        query=query,
        limit=limit,
        stages=stages,
        record_trigger=record_trigger,
    )
    context = format_scored_lessons_context(scored)
    node_ids = [s.node.node_id for s in scored]
    return scored, context, node_ids


# ---------------------------------------------------------------------------
# Feedback loop
# ---------------------------------------------------------------------------
def record_lesson_trigger(node_ids: Sequence[str]) -> int:
    """
    Explicitly increment T (trigger_count) and refresh last_used_at for
    the given lesson node IDs. Call this once per actual LLM injection —
    not from handshake, not from preview commands.
    """
    if not node_ids:
        return 0
    bridge = _get_bridge()
    if bridge is None:
        return 0
    try:
        return bridge.manager.record_trigger(list(node_ids))
    except Exception as exc:
        print(f"[LessonManager] record_trigger failed: {exc}")
        return 0


def record_lesson_helpful(node_ids: Sequence[str]) -> int:
    if not node_ids:
        return 0
    bridge = _get_bridge()
    if bridge is None:
        return 0
    try:
        return bridge.record_helpful(list(node_ids))
    except Exception as exc:
        print(f"[LessonManager] record_helpful failed: {exc}")
        return 0


def record_lesson_violation(node_ids: Sequence[str]) -> int:
    if not node_ids:
        return 0
    bridge = _get_bridge()
    if bridge is None:
        return 0
    try:
        return bridge.record_violation(list(node_ids))
    except Exception as exc:
        print(f"[LessonManager] record_violation failed: {exc}")
        return 0


def analyze_lesson_outcome(
    build_success: bool,
    build_log: str,
    injected_node_ids: Sequence[str],
    keyword_min_hits: int = 2,
) -> tuple:
    """
    Determine which injected lessons deserve H (helpful) vs V (violation).

    Returns (helpful_ids, violated_ids).

    Semantics per the DifferiX blueprint §3B:
      - H increments for every injected lesson when the build passes.
      - V increments only for injected lessons whose distinctive keywords
        appear in the build error output. This prevents penalising a lesson
        that was injected but whose scope was unrelated to the failure.
    """
    if not injected_node_ids:
        return [], []

    if build_success:
        return list(injected_node_ids), []

    bridge = _get_bridge()
    if bridge is None:
        return [], []

    log_lower = (build_log or "").lower()
    if not log_lower.strip():
        return [], []

    import re as _re

    violated: List[str] = []
    for nid in injected_node_ids:
        try:
            node = bridge.manager.get(nid)
        except Exception:
            continue
        md = node.metadata or {}
        details = md.get("details", {}) or {}
        text = " ".join([
            str(md.get("root_cause", "")),
            str(details.get("rule", "")),
            str(details.get("fix_instruction", "")),
            str(details.get("trigger_condition", "")),
        ]).lower()

        keywords = set(_re.findall(r"[a-z][a-z0-9_]{4,}", text))
        if not keywords:
            continue
        hits = sum(1 for kw in keywords if kw in log_lower)
        if hits >= keyword_min_hits:
            violated.append(nid)

    return [], violated


def lesson_memory_stats() -> Dict[str, Any]:
    bridge = _get_bridge()
    if bridge is None:
        return {"enabled": False}
    try:
        st = bridge.stats()
        st["enabled"] = True
        return st
    except Exception as exc:
        return {"enabled": False, "error": str(exc)}


__all__ = [
    "LESSONS_DIRS",
    "init_lessons_dirs",
    "create_observation",
    "move_lesson",
    "move_to_candidates",
    "move_to_promoted",
    "move_to_deprecated",
    "load_lessons_from_category",
    "load_promoted_lessons",
    "get_promoted_lessons_prompt",
    "get_candidates_prompt",
    "get_observations_prompt",
    "get_deprecated_prompt",
    "get_full_memory_prompt",
    "get_lessons_context_for_role",
    "get_ranked_lessons_scored",
    "get_ranked_lessons_context",
    "get_ranked_lessons_for_role",
    "format_scored_lessons_context",
    "analyze_lesson_outcome",
    "record_lesson_trigger",
    "record_lesson_helpful",
    "record_lesson_violation",
    "lesson_memory_stats",
]