import os
import json
import re
import config
from tools.lesson_manager import (
    get_ranked_lessons_for_role,
    get_promoted_lessons_prompt,
)
from tools.skill_manager import get_combined_agent_skill

try:
    from tools.llm_client import safe_chat_completion
except ImportError:
    from llm_client import safe_chat_completion


def get_editor_model() -> str:
    if hasattr(config, "get_active_model") and callable(getattr(config, "get_active_model")):
        return config.get_active_model("editor")
    if hasattr(config, "get_agent_model") and callable(getattr(config, "get_agent_model")):
        return config.get_agent_model("editor")
    return getattr(config, "MODEL_EDITOR", "anthropic/claude-3.7-sonnet")


DIFFERIX_FRAMEWORK_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DIFFERIX_FRAMEWORK_CORE_FILES = {
    ".env", "config.py", "state.json", "deduplicate.py",
    "agents_chat.py", "agents_chat_2.py", "PROJECT_STRUCTURE.md",
}


def resolve_sandboxed_path(target_file: str, project_dir: str = ".") -> str:
    base_path = os.path.abspath(project_dir)
    target_path = os.path.abspath(os.path.join(base_path, target_file))

    try:
        contained = os.path.commonpath([base_path, target_path]) == base_path
    except ValueError:
        contained = False
    if not contained:
        raise PermissionError(
            f"SANDBOX VIOLATION: Path '{target_file}' escapes boundary '{project_dir}'."
        )

    filename = os.path.basename(target_path)
    if filename in DIFFERIX_FRAMEWORK_CORE_FILES:
        target_dir = os.path.dirname(target_path)
        try:
            is_editing_framework_core = os.path.samefile(target_dir, DIFFERIX_FRAMEWORK_ROOT)
        except (FileNotFoundError, AttributeError):
            is_editing_framework_core = (target_dir == DIFFERIX_FRAMEWORK_ROOT)

        if is_editing_framework_core:
            raise PermissionError(
                f"SANDBOX VIOLATION: Editing core framework file '{filename}' in DifferiX root is forbidden."
            )

    return target_path

def _looks_like_full_rewrite(diff_text: str) -> bool:
    if re.search(r"<{4,}\s*SEARCH", diff_text):
        return False

    fences = re.findall(
        r"```(?:\w+)?\n(.*?)```",
        diff_text,
        re.DOTALL,
    )
    if len(fences) != 1:
        return False

    return fences[0].count("\n") >= 30


def apply_diff_blocks(original_content: str, diff_text: str, allow_full_rewrite: bool = False, target_file: str = "") -> dict:
    """Applies SEARCH/REPLACE diff blocks with strict verification."""
    diff_text = diff_text.replace("\r\n", "\n")
    original_content = original_content.replace("\r\n", "\n")

    is_empty_file = not original_content.strip()
    
    # HARD RULE: Never allow full rewrite of an existing HTML file.
    # This prevents the agent from destroying working HTML when confused by a truncated view.
    if target_file.endswith((".html", ".htm")) and len(original_content) > 200:
        allow_full_rewrite = False
        
    full_rewrite_intent = is_empty_file or allow_full_rewrite or _looks_like_full_rewrite(diff_text)

    # Mode A: full rewrite
    if full_rewrite_intent:
        code_match = re.search(
            r"```(?:python|csharp|cs|luau|lua|xml|json|html|css|js|ts|jsx|tsx|yaml|yml|sql|sh|bash|shell)?\n(.*?)```",
            diff_text, re.DOTALL,
        )
        candidate = code_match.group(1).strip() if code_match else diff_text.strip()
        candidate = re.sub(r"^TARGET_FILE:\s*[^\n]+\n?", "", candidate).strip()

        clean_lines = [
            line for line in candidate.splitlines()
            if not re.match(r"^(<{4,}|={4,}|>{4,}|<<<<<<<|=======|>>>>>>>)", line.strip())
        ]
        candidate_code = "\n".join(clean_lines)

        if original_content.strip().startswith("{"):
            try:
                json.loads(original_content)
                json.loads(candidate_code)
            except json.JSONDecodeError:
                return {
                    "ok": False,
                    "code": original_content,
                    "matched": 0,
                    "failed": [{"search_preview": "JSON_VALIDATION_FAILED",
                                "reason": "Candidate output is invalid JSON."}],
                    "mode": "rejected",
                }

        return {"ok": True, "code": candidate_code, "matched": 0,
                "failed": [], "mode": "full_rewrite"}

    # Mode B: SEARCH/REPLACE
    pattern = r"<{4,}\s*SEARCH[ \t]*\n(.*?)\n?={4,}[ \t]*\n(.*?)\n?>{4,}\s*REPLACE"
    matches = re.findall(pattern, diff_text, re.DOTALL)

    if not matches:
        return {
            "ok": False,
            "code": original_content,
            "matched": 0,
            "failed": [{"search_preview": "NO_SEARCH_BLOCKS",
                        "reason": "No SEARCH/REPLACE blocks found in patch for non-empty file."}],
            "mode": "rejected",
        }

    updated_content = original_content
    failed_blocks = []
    matched_count = 0

    for idx, (search_block, replace_block) in enumerate(matches, 1):
        count = updated_content.count(search_block)

        if count == 1:
            updated_content = updated_content.replace(search_block, replace_block, 1)
            matched_count += 1
        elif count > 1:
            failed_blocks.append({
                "block_index": idx,
                "search_preview": search_block[:150] + ("..." if len(search_block) > 150 else ""),
                "reason": f"AMBIGUOUS_MATCH: SEARCH block found {count} times in file.",
            })
        else:
            search_lines_stripped = "\n".join(line.rstrip() for line in search_block.splitlines())
            content_lines_stripped = "\n".join(line.rstrip() for line in updated_content.splitlines())

            if search_lines_stripped and content_lines_stripped.count(search_lines_stripped) == 1:
                start = content_lines_stripped.find(search_lines_stripped)
                end = start + len(search_lines_stripped)
                before_original = updated_content[:start]
                after_original = updated_content[end:]
                updated_content = before_original + replace_block + after_original
                matched_count += 1
            else:
                failed_blocks.append({
                    "block_index": idx,
                    "search_preview": search_block[:150] + ("..." if len(search_block) > 150 else ""),
                    "reason": "NOT_FOUND: SEARCH block does not match current file content verbatim.",
                })

    if failed_blocks:
        return {
            "ok": False,
            "code": original_content,
            "matched": matched_count,
            "failed": failed_blocks,
            "mode": "rejected",
        }

    return {"ok": True, "code": updated_content, "matched": matched_count,
            "failed": [], "mode": "search_replace"}


def _build_retrieval_query(state: dict, target_file: str) -> str:
    parts = [
        str(state.get("active_subtask") or ""),
        str(state.get("research_analysis") or ""),
        str(state.get("goal") or ""),
        str(target_file or ""),
    ]
    query = " ".join(p for p in parts if p).strip()
    return query or "general code editing"


def run_editor(
    state,
    project_files="",
    project_dir=".",
    session_context="",
    related_context="",
):
    from tools.context_assembler import (
        ContextBundle,
        Tier,
        TierItem,
        ContextAssembler,
        smart_slice_code,
        dedupe_logs,
    )

    target_file = state.get("target_file") or "main.py"
    safe_target_path = resolve_sandboxed_path(target_file, project_dir)

    target_code = ""
    if os.path.exists(safe_target_path):
        try:
            with open(safe_target_path, "r", encoding="utf-8") as f:
                target_code = f.read()
        except Exception as e:
            target_code = f"# Error reading file: {e}"

    iteration_logs = state.get("iteration_logs") or state.get("compiler_output", "") or ""
    iteration_number = state.get("iteration_count", 0)

    lessons_block = ""
    if not session_context:
        retrieval_query = _build_retrieval_query(state, target_file)
        _scored, lessons_block, lesson_node_ids = get_ranked_lessons_for_role(
            role="editor",
            query=retrieval_query,
            limit=getattr(config, "LESSONS_RETRIEVAL_LIMIT", 5),
        )
        if not lessons_block:
            lessons_block = get_promoted_lessons_prompt()
        state["active_lesson_node_ids"] = list(lesson_node_ids or [])

    # --- DYNAMIC SKILL INJECTION ---
    active_styles = state.get("web_style", "")
    if isinstance(active_styles, str) and active_styles:
        active_styles = [active_styles]
    elif not isinstance(active_styles, list):
        active_styles = []

    # Always add polish skill if it's a web task
    if any(x.endswith(('.html', '.css', '.js')) for x in [target_file]):
        if "styles/style_polish" not in active_styles:
            active_styles.append("styles/style_polish")

    try:
        system_prompt = get_combined_agent_skill("editor", project_dir, active_styles=active_styles)
    except TypeError:
        # Fallback if skill_manager doesn't support the list yet
        system_prompt = get_combined_agent_skill("editor", project_dir)

    editor_feedback = state.pop("editor_feedback", None)
    editor_payload = {
        "target_file": target_file,
        "goal": state.get("goal", ""),
        "subtask": state.get("active_subtask", ""),
        "research": state.get("research_analysis")
                     or state.get("research_data", {}).get("analysis", ""),
        "style_requirement": ", ".join(active_styles) or "default",
    }
    if editor_feedback:
        editor_payload["REJECTION_FEEDBACK"] = editor_feedback

    is_new_or_empty = not target_code.strip()
    
    file_mode_instruction = (
        "FILE STATUS: NEW OR EMPTY FILE.\n"
        "INSTRUCTION: Output the COMPLETE file code inside a single standard Markdown code block. "
        "Do NOT use SEARCH/REPLACE blocks."
        if is_new_or_empty else
        "FILE STATUS: EXISTING FILE WITH CONTENT.\n"
        "INSTRUCTION: Output one or more SEARCH/REPLACE blocks strictly matching existing code. "
        "Do NOT output the full file. Use the format:\n"
        "<<<<<<< SEARCH\n(verbatim lines from the file)\n=======\n(replacement lines)\n>>>>>>> REPLACE\n"
        "CRITICAL RULE: If the file is truncated and you cannot see the whole thing, "
        "you MUST still use SEARCH/REPLACE targeting the visible lines. Never attempt a full rewrite of an existing HTML file."
    )

    coherence_note = ""
    if related_context:
        coherence_note = (
            "=== CRITICAL: CROSS-FILE COHERENCE ===\n"
            "The RELATED FILES section below shows other files in this project. "
            "You MUST use the exact class names, CSS variables, and function names "
            "already defined there. Do NOT invent new top-level names. If you need "
            "a new class, derive it from an existing prefix (e.g. .hero-title → "
            ".hero-subtitle).\n\n"
        )

    # ---- Build TCA bundle ----
    bundle = ContextBundle()

    # T0: system prompt (cacheable prefix)
    bundle.add_text(Tier.T0_STATIC, "skills", system_prompt)

    # T1: action core — spec + file mode + coherence note + session brief
    action_core = (
        f"{coherence_note}"
        f"=== FILE WRITING MODE ===\n{file_mode_instruction}\n\n"
        f"=== SPECIFICATION ===\n{json.dumps(editor_payload, ensure_ascii=False)}"
    )
    bundle.add_text(Tier.T1_ACTION_CORE, "spec", action_core)

    if session_context:
        bundle.add_text(Tier.T1_ACTION_CORE, "session", session_context)

    # T2: target file, sliced for prompt (full file still used for apply)
    if target_code:
        if is_new_or_empty:
            bundle.add_text(
                Tier.T2_TARGET_SLICE,
                "target",
                f"=== TARGET FILE ({target_file}) ===\n[empty — create new file]",
            )
        else:
            hints = " ".join(filter(None, [
                str(state.get("active_subtask") or ""),
                str(state.get("research_analysis") or "")[:400],
            ]))
            sliced = smart_slice_code(
                target_code, target_file, max_lines=150, hints=hints
            )
            tightly = smart_slice_code(
                target_code, target_file, max_lines=50, hints=hints
            )
            bundle.add(TierItem(
                tier=Tier.T2_TARGET_SLICE,
                key="target",
                content=f"=== TARGET FILE ({target_file}) ===\n{sliced}",
                fallback_content=(
                    f"=== TARGET FILE ({target_file}) [HEAVILY TRUNCATED] ===\n{tightly}"
                    if tightly != sliced else ""
                ),
                optional=False,
            ))

    # T3: related files (sibling interfaces)
    if related_context:
        bundle.add_text(Tier.T3_INTERFACE, "related", related_context)

    # T4: lessons
    if lessons_block:
        bundle.add_text(Tier.T4_ENRICHMENT, "lessons", lessons_block)

    # T5: deduped logs (skip on first cycle by default)
    if iteration_logs.strip():
        include_logs = (
            iteration_number > 0
            or getattr(config, "TCA_INCLUDE_FEEDBACK_ON_FIRST_CYCLE", False)
        )
        if include_logs:
            deduped = dedupe_logs(iteration_logs, max_errors=5)
            if deduped:
                bundle.add_text(
                    Tier.T5_FEEDBACK,
                    "logs",
                    f"=== RECENT ERRORS (deduplicated) ===\n{deduped}",
                    optional=True,
                )

    # ---- Assemble ----
    budget = getattr(config, "TOKEN_BUDGET_EDITOR", 18000)
    assembled = ContextAssembler(
        budget=budget,
        safety_margin=getattr(config, "TCA_SAFETY_MARGIN", 0.9),
    ).assemble(bundle)

    if getattr(config, "TCA_LOG_SUMMARY", True):
        try:
            print(assembled.log_summary())
        except Exception:
            pass

    # ---- Call LLM ----
    extra_body_params = {"reasoning": {"max_tokens": 8192}}
    active_model = get_editor_model()

    response, provider = safe_chat_completion(
        active_model,
        messages=[
            {"role": "system", "content": assembled.system_prompt},
            {"role": "user", "content": assembled.user_prompt},
        ],
        temperature=0.15,
        max_tokens=8192,
        extra_body=extra_body_params,
    )

    if not response or not getattr(response, "choices", None) or len(response.choices) == 0:
        raise RuntimeError(
            f"Error: Editor received no choices from model provider ({active_model})."
        )

    choice = response.choices[0]
    message = getattr(choice, "message", None)
    finish_reason = getattr(choice, "finish_reason", None)

    result_text = ""
    if message:
        result_text = getattr(message, "content", "") or ""
        if not result_text.strip() and hasattr(message, "reasoning_content"):
            result_text = getattr(message, "reasoning_content", "") or ""

    result_text = result_text.strip()

    if not result_text:
        if finish_reason == "length":
            raise RuntimeError(
                f"Error: Editor response was truncated due to max_tokens limit "
                f"(finish_reason='length'). Model: {active_model}"
            )
        raise RuntimeError(
            f"Error: Editor received empty response from model ({active_model}). "
            f"finish_reason: {finish_reason}"
        )

    # Reload target_code if the model requested a different file.
    target_file_match = re.search(r"TARGET_FILE:\s*([^\n]+)", result_text)
    if target_file_match:
        raw_path = target_file_match.group(1).strip()
        target_file = re.sub(r'[`"\'\s]', '', raw_path)
        safe_target_path = resolve_sandboxed_path(target_file, project_dir)
        if os.path.exists(safe_target_path):
            try:
                with open(safe_target_path, "r", encoding="utf-8") as f:
                    target_code = f.read()
            except Exception:
                pass

    # Apply against FULL file, passing target_file for the HTML rewrite guard
    apply_res = apply_diff_blocks(
        target_code,
        result_text,
        allow_full_rewrite=state.get("allow_full_rewrite", False),
        target_file=target_file,
    )

    state["generated_code"] = {
        "target_file": target_file,
        "action": "modify",
        "code": apply_res["code"],
        "apply_result": apply_res,
    }

    usage = getattr(response, "usage", None)
    usage_meta = type("Meta", (), {
        "prompt_token_count": getattr(usage, "prompt_tokens", 0) or 0,
        "candidates_token_count": getattr(usage, "completion_tokens", 0) or 0,
        "raw_response": result_text,
        "provider": provider,
        "model_id": active_model,
    })

    return state, usage_meta