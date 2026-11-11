"""
tools/cost_predictor.py
-----------------------
Lightweight pre-flight cost estimator for the DifferiX pipeline.

Given a researcher plan (a list of step dicts) it routes each step to a
model via the same complexity assessment used at call time, prices the
expected token budget with that model's published rates, and returns a
per-step breakdown plus a padded grand total.

Public API:
    predict_cost(plan: list[dict], task_prompt: str = "") -> dict
"""

from __future__ import annotations

from typing import Any, Dict, List

from tools.llm_selector import (
    assess_task_complexity,
    select_model_for_task,
    get_model_details,
)


INPUT_TOKENS_FIXED: int = 6000
DEFAULT_ESTIMATED_LINES: int = 120
MIN_ESTIMATED_LINES: int = 50
TOKENS_PER_LINE: int = 20
OVERHEAD_MULTIPLIER: float = 1.3
DEFAULT_COMPLEXITY: str = "medium"


__all__ = ["predict_cost"]


def _safe_complexity(text: str) -> Any:
    try:
        result = assess_task_complexity(text)
    except Exception:
        return DEFAULT_COMPLEXITY
    if result is None:
        return DEFAULT_COMPLEXITY
    return result


def _safe_model(complexity: Any) -> str:
    try:
        model = select_model_for_task(complexity)
    except Exception:
        return ""
    return model or ""


def _safe_pricing(model: str) -> Dict[str, Any]:
    if not model:
        return {}
    try:
        pricing = get_model_details(model)
    except Exception:
        return {}
    if not isinstance(pricing, dict):
        return {}
    return pricing


def _safe_float(value: Any) -> float:
    try:
        if value is None:
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _resolve_estimated_lines(raw: Any) -> int:
    try:
        value = int(raw) if raw is not None else None
    except (TypeError, ValueError):
        value = None
    if not value:
        value = DEFAULT_ESTIMATED_LINES
    return max(value, MIN_ESTIMATED_LINES)


def _step_fields(step: Any) -> tuple:
    if isinstance(step, dict):
        return step.get("title"), step.get("id"), step.get("estimated_lines")
    return None, None, None


def predict_cost(plan: List[Dict[str, Any]], task_prompt: str = "") -> Dict[str, Any]:
    if not isinstance(plan, (list, tuple)):
        return {"total_usd": 0.0, "by_step": []}

    by_step: List[Dict[str, Any]] = []

    for index, step in enumerate(plan):
        title, step_id, raw_lines = _step_fields(step)

        if title is None or not str(title).strip():
            task_text = task_prompt or ""
            display_title = ""
        else:
            task_text = str(title)
            display_title = str(title)

        if step_id is None:
            step_id = index + 1

        complexity = _safe_complexity(task_text)
        model = _safe_model(complexity)
        pricing = _safe_pricing(model)

        input_tokens = INPUT_TOKENS_FIXED
        estimated_lines = _resolve_estimated_lines(raw_lines)
        output_tokens = estimated_lines * TOKENS_PER_LINE

        price_input = _safe_float(pricing.get("price_input_1m"))
        price_output = _safe_float(pricing.get("price_output_1m"))

        cost_usd = (
            (input_tokens * price_input / 1_000_000.0)
            + (output_tokens * price_output / 1_000_000.0)
        )

        by_step.append(
            {
                "step_id": step_id,
                "title": display_title,
                "model": model,
                "complexity": complexity,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost_usd": cost_usd,
            }
        )

    total_usd = sum(entry["cost_usd"] for entry in by_step) * OVERHEAD_MULTIPLIER

    return {"total_usd": total_usd, "by_step": by_step}