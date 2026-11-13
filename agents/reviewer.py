# ===================== agents/reviewer.py =====================
import os
import json
import re
import config
from tools.lesson_manager import get_promoted_lessons_prompt, create_observation
from tools.skill_manager import get_combined_agent_skill

try:
    from tools.llm_client import safe_chat_completion
except ImportError:
    from llm_client import safe_chat_completion


def get_reviewer_model() -> str:
    if hasattr(config, "get_active_model") and callable(getattr(config, "get_active_model")):
        return config.get_active_model("reviewer")
    if hasattr(config, "get_agent_model") and callable(getattr(config, "get_agent_model")):
        return config.get_agent_model("reviewer")
    return getattr(config, "MODEL_REVIEWER", "google/gemini-2.5-pro")


def resolve_sandboxed_path(target_file: str, project_dir: str = ".") -> str:
    base_path = os.path.abspath(project_dir)
    target_path = os.path.abspath(os.path.join(base_path, target_file))
    try:
        contained = os.path.commonpath([base_path, target_path]) == base_path
    except ValueError:
        contained = False
    if not contained:
        raise PermissionError(
            f"SANDBOX VIOLATION: Reviewer path '{target_file}' escapes boundary."
        )
    return target_path


def _try_partial_json_extract(text: str) -> dict:
    result: dict = {}
    m = re.search(r'"is_approved"\s*:\s*(true|false)', text, re.IGNORECASE)
    if m:
        result["is_approved"] = m.group(1).lower() == "true"
    m = re.search(r'"verdict"\s*:\s*"(ACCEPT|REJECT)"', text, re.IGNORECASE)
    if m:
        result["verdict"] = m.group(1).upper()
    m = re.search(r'"reason"\s*:\s*"(.*)$', text, re.DOTALL)
    if m:
        raw = m.group(1)
        cleaned = re.split(r'(?<!\\)"', raw, maxsplit=1)[0]
        result["reason"] = cleaned.strip()
    return result


def parse_reviewer_payload(raw_text: str) -> dict:
    if not raw_text or not isinstance(raw_text, str):
        return {
            "is_approved": False,
            "verdict": "REJECT",
            "root_cause": "Empty model output",
            "reason": "Reviewer received empty text from LLM.",
        }

    text = raw_text.strip()

    # Prefer fenced JSON
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence_match:
        try:
            return json.loads(fence_match.group(1))
        except json.JSONDecodeError:
            pass

    # Prefer outermost { ... }
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Partial extract
    if text.startswith("{") or text.startswith("```"):
        partial = _try_partial_json_extract(text)
        if "is_approved" in partial or "verdict" in partial:
            return {
                "is_approved": partial.get("is_approved", False),
                "verdict": partial.get("verdict", "REJECT"),
                "reason": partial.get("reason", text[:200]),
                "_partial_parse": True,
            }

    # Very strict free-text fallback: only accept if explicit positive
    # markers are present AND no negative markers exist.
    upper_text = text.upper()
    negative = any(
        kw in upper_text
        for kw in (
            "REJECT",
            "NOT ACCEPT",
            "DO NOT ACCEPT",
            "CANNOT ACCEPT",
            "REFUSE",
            "FAILED",
            "BROKEN",
            "SYNTAX ERROR",
            "BUILD_FAILED",
        )
    )
    positive = any(
        kw in upper_text
        for kw in (
            "STATUS:** APPROVED",
            "STATUS: APPROVED",
            "VERDICT: ACCEPT",
            "VERDICT: APPROVED",
            '"IS_APPROVED": TRUE',
            '"VERDICT": "ACCEPT"',
        )
    )

    is_accept = positive and not negative

    reason_match = re.search(r"\*\*Analysis:\*\*\s*(.+)", text, re.DOTALL)
    clean_reason = reason_match.group(1).strip() if reason_match else text[:300]

    return {
        "is_approved": is_accept,
        "verdict": "ACCEPT" if is_accept else "REJECT",
        "root_cause": (
            "Parsed from Markdown text response."
            if is_accept
            else "Reviewer rejected changes."
        ),
        "reason": clean_reason,
    }


def _compiler_indicates_failure(compiler_log: str) -> bool:
    """Machine check: does the compiler log clearly signal a failure?"""
    if not compiler_log:
        return False
    upper = compiler_log.upper()
    failure_markers = (
        "BUILD_FAILED",
        "SYNTAX ERROR",
        "JS SYNTAX",
        "TYPESCRIPT",
        "UNBALANCED",
        "UNEXPECTED TOKEN",
        "UNEXPECTED CLOSING",
        "MISMATCHED",
        "UNCLOSED",
        "PARSE ERROR",
        "SYNTAXERROR",
    )
    # SUCCESS (SKIPPED) is not a failure
    if "BUILD_FAILED" in upper:
        return True
    if any(m in upper for m in failure_markers) and "SUCCESS" not in upper[:40]:
        return True
    return False


def run_reviewer(
    state,
    project_dir=".",
    session_context="",
    coherence_report="",
    visual_context="",
):
    from tools.context_assembler import (
        ContextBundle,
        Tier,
        TierItem,
        ContextAssembler,
        smart_slice_code,
        dedupe_logs,
        render_code_diff,
    )

    if session_context:
        promoted_lessons = ""
    else:
        from tools.lesson_manager import get_ranked_lessons_for_role

        retrieval_query = " ".join(
            filter(
                None,
                [
                    str(state.get("active_subtask") or ""),
                    str(state.get("research_analysis") or ""),
                    str(state.get("goal") or ""),
                    str(state.get("target_file") or ""),
                ],
            )
        ).strip() or "code review"

        _, lessons_block, _ = get_ranked_lessons_for_role(
            role="reviewer",
            query=retrieval_query,
            limit=getattr(config, "LESSONS_RETRIEVAL_LIMIT", 5),
        )
        promoted_lessons = lessons_block or get_promoted_lessons_prompt()

    iteration_logs = state.get("iteration_logs") or state.get("compiler_output", "") or ""
    skill_prompt = get_combined_agent_skill("reviewer", project_dir)

    coherence_rule = ""
    if coherence_report:
        coherence_rule = (
            "\n- If the COHERENCE REPORT below contains any ERROR-level finding, "
            "you MUST set is_approved to false. Cross-file coherence violations "
            "are blocking and cannot be overridden by style or completeness."
        )

    system_prompt = (
        f"{skill_prompt}\n\n"
        f"REVIEW PRIORITY GUIDELINES:\n"
        f"- CRITICAL (blocking): broken syntax, missing files, broken cross-file "
        f"references, security violations, dead code, TODO stubs, "
        f"BUILD_FAILED / JS SYNTAX ERROR / unbalanced braces.\n"
        f"- WARNING (non-blocking): missing prefers-reduced-motion guards, "
        f"style inconsistencies, minor accessibility gaps. Mention them in "
        f"`reason` but do NOT set is_approved=false for these alone.\n"
        f"- Only set is_approved=false if there is a CRITICAL issue.\n"
        f"- If the EXECUTION LOGS or compiler output contain BUILD_FAILED, "
        f"SYNTAX ERROR, or JS SYNTAX — you MUST reject.\n\n"
        f"CRITICAL REQUIREMENT:\n"
        f"You MUST respond ONLY with a raw, valid JSON object matching this "
        f"schema:\n"
        f'{{"is_approved": true, "verdict": "ACCEPT", "reason": "Short summary."}}\n'
        f"Rules:\n"
        f"- reason MUST be under 150 characters.\n"
        f"- Do NOT write Markdown headers or prose outside the JSON.\n"
        f"- Do NOT nest JSON inside fields.\n"
        f"- The first character of your response MUST be {{ and the last MUST be }}."
        f"{coherence_rule}"
    )

    compiler_log = str(state.get("compiler_output", ""))
    target_file = state.get("active_target_file") or state.get("target_file")

    generated_data = state.get("generated_code")
    code_content = (
        generated_data.get("code")
        if isinstance(generated_data, dict)
        else generated_data
    )
    apply_result = (
        generated_data.get("apply_result")
        if isinstance(generated_data, dict)
        else None
    )

    if target_file and not code_content:
        try:
            safe_target_path = resolve_sandboxed_path(target_file, project_dir)
            if os.path.exists(safe_target_path):
                with open(safe_target_path, "r", encoding="utf-8") as f:
                    code_content = f.read()
        except PermissionError as pe:
            code_content = f"SANDBOX REJECTION: {pe}"

    pruned_state = {
        "iteration": state.get("iteration_count", 0),
        "active_subtask": state.get("active_subtask", ""),
        "target_file": target_file,
    }
    if isinstance(apply_result, dict):
        pruned_state["patch_apply_status"] = {
            "ok": apply_result.get("ok"),
            "mode": apply_result.get("mode"),
            "matched": apply_result.get("matched"),
            "failed": apply_result.get("failed"),
        }
    injected_ids = state.get("active_lesson_node_ids") or []
    if injected_ids:
        pruned_state["injected_lesson_node_ids"] = list(injected_ids)

    bundle = ContextBundle()
    bundle.add_text(Tier.T0_STATIC, "skills", system_prompt)
    bundle.add_text(
        Tier.T1_ACTION_CORE,
        "state",
        f"=== STATE TO REVIEW ===\n{json.dumps(pruned_state, ensure_ascii=False)}",
    )

    if code_content:
        old_code = state.get("old_code_backup", "") or ""
        diff_view = render_code_diff(old_code, code_content, target_file or "file")

        if diff_view:
            bundle.add_text(Tier.T2_TARGET_SLICE, "diff", diff_view)
        else:
            hints = " ".join(
                filter(
                    None,
                    [
                        str(state.get("active_subtask") or ""),
                        str(state.get("research_analysis") or "")[:400],
                    ],
                )
            )
            sliced = smart_slice_code(
                code_content, target_file or "file", max_lines=200, hints=hints,
            )
            tightly = smart_slice_code(
                code_content, target_file or "file", max_lines=60, hints=hints,
            )
            bundle.add(
                TierItem(
                    tier=Tier.T2_TARGET_SLICE,
                    key="code",
                    content=f"=== CODE TO REVIEW ({target_file}) ===\n{sliced}",
                    fallback_content=(
                        f"=== CODE TO REVIEW ({target_file}) [HEAVILY TRUNCATED] ===\n{tightly}"
                        if tightly != sliced
                        else ""
                    ),
                    optional=False,
                )
            )

    if coherence_report:
        bundle.add_text(
            Tier.T3_INTERFACE,
            "coherence",
            "=== COHERENCE REPORT (machine-generated, authoritative) ===\n"
            + coherence_report,
        )
    if visual_context:
        bundle.add_text(
            Tier.T3_INTERFACE,
            "visual",
            "=== VISUAL REVIEW (machine-generated) ===\n" + visual_context,
        )
    if session_context:
        bundle.add_text(Tier.T3_INTERFACE, "session", session_context)

    if promoted_lessons:
        bundle.add_text(
            Tier.T4_ENRICHMENT,
            "lessons",
            f"=== RELEVANT LESSONS (A-SCORE RANKED) ===\n{promoted_lessons}",
        )

    if iteration_logs.strip():
        deduped = dedupe_logs(iteration_logs, max_errors=5)
        if deduped:
            bundle.add_text(
                Tier.T5_FEEDBACK,
                "logs",
                f"=== RECENT EXECUTION LOGS (deduplicated) ===\n{deduped}",
                optional=True,
            )

    budget = getattr(config, "TOKEN_BUDGET_REVIEWER", 14000)
    assembled = ContextAssembler(
        budget=budget,
        safety_margin=getattr(config, "TCA_SAFETY_MARGIN", 0.9),
    ).assemble(bundle)

    if getattr(config, "TCA_LOG_SUMMARY", True):
        try:
            print(assembled.log_summary())
        except Exception:
            pass

    active_model = get_reviewer_model()

    response, provider = safe_chat_completion(
        role_or_model="reviewer",
        messages=[
            {"role": "system", "content": assembled.system_prompt},
            {"role": "user", "content": assembled.user_prompt},
        ],
        temperature=0.1,
        max_tokens=4096,
        task_spec=str(state.get("active_subtask") or state.get("target_file") or ""),
    )

    if not response or not getattr(response, "choices", None) or not response.choices:
        raise RuntimeError("Reviewer: Empty response from model.")

    result_text = (response.choices[0].message.content or "").strip()
    result = parse_reviewer_payload(result_text)

    raw_approved = result.get("is_approved")
    if isinstance(raw_approved, str):
        is_approved = raw_approved.lower() in ("true", "1", "yes")
    elif isinstance(raw_approved, bool):
        is_approved = raw_approved
    else:
        is_approved = str(result.get("verdict", "")).upper() == "ACCEPT"

    # ------------------------------------------------------------------
    # MACHINE OVERRIDE: never trust LLM ACCEPT when compiler already failed
    # ------------------------------------------------------------------
    if is_approved and _compiler_indicates_failure(compiler_log):
        is_approved = False
        result["reason"] = (
            "Machine override: compiler log indicates syntax/build failure. "
            + (result.get("reason") or "")
        )[:150]
        result["_machine_override"] = True

    result["is_approved"] = is_approved
    result["verdict"] = "ACCEPT" if is_approved else "REJECT"

    if not result.get("reason"):
        result["reason"] = (
            result.get("root_cause")
            or result.get("fix_instruction")
            or (
                "Edits approved successfully."
                if is_approved
                else "Edits rejected by reviewer."
            )
        )

    if not is_approved and result.get("create_observation"):
        obs_file = create_observation(
            root_cause=result.get("root_cause", "Reviewer rejected implementation"),
            details={"fix_instruction": result.get("fix_instruction", "")},
        )
        print(f"[Lesson Manager] Observation created by Reviewer: {obs_file}")

    state["review_result"] = result

    usage = getattr(response, "usage", None)
    usage_meta = type(
        "Meta",
        (),
        {
            "prompt_token_count": getattr(usage, "prompt_tokens", 0) or 0,
            "candidates_token_count": getattr(usage, "completion_tokens", 0) or 0,
            "raw_response": result_text,
            "provider": provider,
            "model_id": active_model,
        },
    )

    return state, usage_meta