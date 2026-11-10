"""
tools/reflection.py
-------------------
Periodic self-reflection over recent failed cycles.

Runs every N iterations (config.REFLECTION_INTERVAL). Reads recent
iteration logs, clusters failures by normalized reason, and asks an LLM
to extract generalizable observations. Output observations are written
to lessons/observations/ with _auto_generated: true.

The observations then flow through the existing lifecycle:
  observations → SQLite (via bridge) → A-Score ranking
  → eventually promoted by human or by sustained helpful count.

Never raises. Never blocks the pipeline. Worst case, it produces zero
observations and the pipeline continues.
"""
from __future__ import annotations

import json
import logging
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import config

logger = logging.getLogger(__name__)


def _reflection_interval() -> int:
    return int(getattr(config, "REFLECTION_INTERVAL", 5))


def _reflection_model() -> str:
    # Reflection shapes future behavior; use the top-tier model unless
    # explicitly overridden.
    explicit = getattr(config, "REFLECTION_MODEL", None)
    if explicit:
        return explicit
    try:
        from tools.llm_selector import MODEL_TIERS
        high = MODEL_TIERS.get("high") or []
        if high:
            return high[0]
    except Exception:
        pass
    return "anthropic/claude-sonnet-4.5"


def _max_observations_per_run() -> int:
    return int(getattr(config, "REFLECTION_MAX_OBSERVATIONS", 3))


def _min_pattern_occurrences() -> int:
    return int(getattr(config, "REFLECTION_MIN_OCCURRENCES", 2))


@dataclass
class ReflectionResult:
    success: bool
    observations_written: List[str] = field(default_factory=list)
    observations_skipped: List[str] = field(default_factory=list)
    failures_analyzed: int = 0
    patterns_found: int = 0
    error: str = ""

    def as_dict(self) -> Dict:
        return {
            "success": self.success,
            "observations_written": self.observations_written,
            "observations_skipped": self.observations_skipped,
            "failures_analyzed": self.failures_analyzed,
            "patterns_found": self.patterns_found,
            "error": self.error,
        }


def _load_recent_logs(project_dir: str, limit: int = 30) -> List[Dict[str, Any]]:
    logs_dir = Path(project_dir) / "iteration_logs"
    if not logs_dir.is_dir():
        return []
    files = sorted(
        logs_dir.glob("*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[:limit]
    out: List[Dict[str, Any]] = []
    for p in files:
        try:
            with p.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                data["_source_file"] = p.name
                out.append(data)
        except Exception as exc:
            logger.debug("Skipping unreadable log %s: %s", p, exc)
    return out


def _extract_failures(logs: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    failures: List[Dict[str, str]] = []
    for log in logs:
        iteration = log.get("iteration_count") or log.get("iteration") or 0
        reason = ""
        target = log.get("active_target_file") or log.get("target_file") or ""

        review = log.get("review_result") or {}
        if isinstance(review, dict):
            verdict = str(review.get("verdict", "")).upper()
            if verdict == "REJECT":
                reason = review.get("reason") or review.get("feedback") or "rejected"

        compiler = str(log.get("compiler_output") or "")
        if not reason and "REVIEWER_REJECTED" in compiler:
            match = re.search(r"REVIEWER_REJECTED:\s*(.+)", compiler)
            reason = match.group(1).strip() if match else "rejected"

        if not reason:
            status = str(log.get("status") or "").lower()
            if status == "failed":
                reason = compiler[:200] or "failed"

        if reason:
            failures.append({
                "cycle": str(iteration),
                "reason": reason[:400],
                "target_file": target,
                "kind": "reviewer_reject" if "REJECT" in compiler else "cycle_failure",
            })
    return failures


def _normalize_reason(reason: str) -> str:
    text = (reason or "").lower()
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(
        r"\b(contains?|has|have|found|blocking|across|project|file|files|the|a|an)\b",
        " ",
        text,
    )
    text = re.sub(r"\s+", " ", text).strip()
    return " ".join(text.split()[:10])


def _cluster_failures(failures: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    groups: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    for f in failures:
        key = _normalize_reason(f["reason"])
        if key:
            groups[key].append(f)

    threshold = _min_pattern_occurrences()
    clusters = []
    for key, members in groups.items():
        if len(members) >= threshold:
            clusters.append({
                "normalized_key": key,
                "count": len(members),
                "sample_reason": members[0]["reason"],
                "sample_targets": list({m["target_file"] for m in members if m["target_file"]}),
                "cycles": [m["cycle"] for m in members],
            })
    clusters.sort(key=lambda c: c["count"], reverse=True)
    return clusters


def _existing_lesson_ids(project_dir: str) -> set:
    ids = set()
    for stage in ("observations", "candidates", "promoted", "deprecated"):
        stage_dir = Path(project_dir) / "lessons" / stage
        if not stage_dir.is_dir():
            continue
        for p in stage_dir.glob("*.json"):
            ids.add(p.stem)
    return ids


def _similar_root_cause_exists(
    candidate_root_cause: str,
    existing_lessons: List[Dict[str, Any]],
    threshold: float = 0.75,
) -> bool:
    def tokens(s: str) -> set:
        return set(re.findall(r"[a-z]{4,}", (s or "").lower()))

    new_tokens = tokens(candidate_root_cause)
    if not new_tokens:
        return False
    for lesson in existing_lessons:
        existing = tokens(lesson.get("root_cause", ""))
        if not existing:
            continue
        inter = new_tokens & existing
        union = new_tokens | existing
        if union and len(inter) / len(union) >= threshold:
            return True
    return False


def _load_all_existing_lessons(project_dir: str) -> List[Dict[str, Any]]:
    out = []
    for stage in ("observations", "candidates", "promoted"):
        stage_dir = Path(project_dir) / "lessons" / stage
        if not stage_dir.is_dir():
            continue
        for p in stage_dir.glob("*.json"):
            try:
                with p.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    out.append(data)
            except Exception:
                pass
    return out


_REFLECTION_SYSTEM_PROMPT = """You are a self-reflection agent for an AI coding pipeline.

You receive a summary of recent FAILED cycles. Your job is to find PATTERNS
across failures, not to fix individual bugs.

Rules:
1. Only propose an observation if the same underlying pattern appears
   at least twice in the input.
2. Each observation must be a general, reusable rule. Not a specific fix.
   Bad: "add the class .card-title to styles.css"
   Good: "every HTML class must have a matching CSS rule in the stylesheet"
3. Output at most {max_obs} observations.
4. If nothing generalizable can be said, output an empty list. Better to
   output zero observations than one fabricated pattern.
5. Each observation must have a short snake_case id, a one-sentence
   root_cause, and a one-sentence details.rule.

Respond ONLY with a valid JSON object in this exact shape:
{{
  "observations": [
    {{
      "id": "short_snake_case_id",
      "root_cause": "one sentence",
      "details": {{"rule": "one sentence"}}
    }}
  ]
}}
"""


def _build_reflection_prompt(clusters: List[Dict[str, Any]]) -> str:
    parts = ["Recent failure patterns:\n"]
    for i, c in enumerate(clusters, 1):
        parts.append(f"Pattern {i} (occurred {c['count']} times):")
        parts.append(f"  Sample reason: {c['sample_reason']}")
        if c["sample_targets"]:
            parts.append(f"  Files involved: {', '.join(c['sample_targets'][:3])}")
        parts.append("")
    return "\n".join(parts)


def _call_reflection_llm(prompt: str) -> Optional[Dict[str, Any]]:
    try:
        from tools.llm_client import safe_chat_completion
    except ImportError:
        logger.warning("llm_client not importable; reflection skipped")
        return None

    system_prompt = _REFLECTION_SYSTEM_PROMPT.format(
        max_obs=_max_observations_per_run()
    )

    try:
        response, _ = safe_chat_completion(
            role_or_model="researcher",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=1500,
            model=_reflection_model(),
        )
    except Exception as exc:
        logger.warning("Reflection LLM call failed: %s", exc)
        return None

    if not response or not getattr(response, "choices", None):
        return None

    raw = (response.choices[0].message.content or "").strip()
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if m:
        raw = m.group(1)
    else:
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end > start:
            raw = raw[start:end + 1]

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Reflection: could not parse JSON from LLM output")
        return None


def _slugify(raw: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (raw or "").lower()).strip("_")
    return s[:60] or "auto_observation"


def _write_observation(
    project_dir: str,
    observation: Dict[str, Any],
    source_clusters: List[Dict[str, Any]],
) -> Optional[str]:
    raw_id = observation.get("id") or ""
    obs_id = _slugify(raw_id)
    if not obs_id:
        return None

    root_cause = str(observation.get("root_cause") or "").strip()
    rule = str((observation.get("details") or {}).get("rule") or "").strip()
    if not root_cause or not rule:
        return None

    obs_dir = Path(project_dir) / "lessons" / "observations"
    obs_dir.mkdir(parents=True, exist_ok=True)

    target = obs_dir / f"{obs_id}.json"
    if target.exists():
        return None

    payload = {
        "id": obs_id,
        "root_cause": root_cause,
        "details": {"rule": rule},
        "_auto_generated": True,
        "_source": "reflector",
        "_generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "_source_patterns": [
            {"count": c["count"], "sample": c["sample_reason"][:200]}
            for c in source_clusters
        ],
    }

    try:
        target.write_text(
            json.dumps(payload, indent=4, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError as exc:
        logger.warning("Failed to write observation %s: %s", obs_id, exc)
        return None

    return obs_id


def should_reflect(iteration: int) -> bool:
    interval = _reflection_interval()
    return interval > 0 and iteration > 0 and iteration % interval == 0


def run_reflection(
    state: Dict[str, Any],
    project_dir: str = ".",
    force: bool = False,
) -> ReflectionResult:
    """
    Run one reflection cycle. Never raises.

    force=True ignores the minimum-occurrence threshold. Use for the
    manual `vegex reflect` command.
    """
    result = ReflectionResult(success=False)
    try:
        logs = _load_recent_logs(project_dir, limit=30)
        failures = _extract_failures(logs)
        result.failures_analyzed = len(failures)

        if not failures:
            result.success = True
            result.error = "no recent failures to reflect on"
            return result

        clusters = _cluster_failures(failures)
        if not clusters and not force:
            result.success = True
            result.error = "no repeated patterns found"
            return result

        if not clusters:
            seen: Dict[str, Dict[str, Any]] = {}
            for f in failures:
                key = _normalize_reason(f["reason"])
                if key and key not in seen:
                    seen[key] = {
                        "normalized_key": key,
                        "count": 1,
                        "sample_reason": f["reason"],
                        "sample_targets": [f["target_file"]] if f["target_file"] else [],
                        "cycles": [f["cycle"]],
                    }
            clusters = list(seen.values())[:_max_observations_per_run() * 2]

        result.patterns_found = len(clusters)

        prompt = _build_reflection_prompt(clusters)
        parsed = _call_reflection_llm(prompt)
        if not parsed:
            result.error = "LLM call or parse failed"
            return result

        raw_observations = parsed.get("observations") or []
        if not isinstance(raw_observations, list):
            result.error = "observations field is not a list"
            return result

        existing_lessons = _load_all_existing_lessons(project_dir)
        existing_ids = _existing_lesson_ids(project_dir)

        for obs in raw_observations[:_max_observations_per_run()]:
            if not isinstance(obs, dict):
                continue
            obs_id = _slugify(obs.get("id", ""))
            if not obs_id:
                continue
            if obs_id in existing_ids:
                result.observations_skipped.append(obs_id)
                continue
            if _similar_root_cause_exists(obs.get("root_cause", ""), existing_lessons):
                result.observations_skipped.append(obs_id)
                continue

            written = _write_observation(project_dir, obs, clusters)
            if written:
                result.observations_written.append(written)
                existing_ids.add(written)
            else:
                result.observations_skipped.append(obs_id)

        result.success = True
        return result

    except Exception as exc:
        logger.exception("Reflection failed")
        result.error = f"unhandled exception: {exc}"
        return result


__all__ = ["ReflectionResult", "run_reflection", "should_reflect"]