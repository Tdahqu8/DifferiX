import os
import json
import re
import config
from tools.lesson_manager import (
    get_promoted_lessons_prompt,
    get_candidates_prompt,
    get_observations_prompt,
    get_deprecated_prompt,
    create_observation,
    get_ranked_lessons_for_role,
    get_full_memory_prompt,
)
from tools.skill_manager import get_combined_agent_skill

from tools.llm_client import safe_chat_completion

RESEARCHER_MAX_TOKENS = getattr(config, "RESEARCHER_MAX_TOKENS", 6000)
RESEARCHER_RETRY_MAX_TOKENS = getattr(config, "RESEARCHER_RETRY_MAX_TOKENS", 12000)
LESSONS_BLOCK_CHAR_LIMIT = getattr(config, "RESEARCHER_LESSONS_CHAR_LIMIT", 3000)
LOG_CHAR_LIMIT = 1500


def get_researcher_model() -> str:
    if hasattr(config, "get_active_model") and callable(getattr(config, "get_active_model")):
        return config.get_active_model("researcher")
    if hasattr(config, "get_agent_model") and callable(getattr(config, "get_agent_model")):
        return config.get_agent_model("researcher")
    return getattr(config, "MODEL_RESEARCHER", "anthropic/claude-3.5-sonnet")


def get_project_file_tree(project_dir: str = ".") -> str:
    tree = []
    base_path = os.path.abspath(project_dir)
    ignored_dirs = {
        ".git", "__pycache__", ".venv", "venv",
        "node_modules", ".idea", ".vscode", ".vegex",
    }

    for root, dirs, files in os.walk(base_path):
        dirs[:] = [d for d in dirs if d not in ignored_dirs and not d.startswith(".")]
        rel_root = os.path.relpath(root, base_path)
        if rel_root == ".":
            rel_root = ""

        for f in files:
            if f.startswith(".") or f.endswith((".pyc", ".png", ".jpg", ".zip", ".tar", ".gz")):
                continue
            path_str = os.path.join(rel_root, f) if rel_root else f
            tree.append(path_str)
            if len(tree) >= 100:
                tree.append("... [TRUNCATED FILE TREE]")
                return "\n".join(tree)

    return "\n".join(tree) if tree else "Empty directory."


def parse_researcher_payload(raw_text: str) -> dict:
    if not raw_text or not isinstance(raw_text, str):
        return {}

    text = raw_text.strip()

    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence_match:
        try:
            return json.loads(fence_match.group(1))
        except json.JSONDecodeError:
            pass

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    return {
        "analysis": text,
        "technical_plan": [],
        "_parse_failed": True,
    }


def _clip(text, limit: int, keep: str = "head") -> str:
    text = "" if text is None else str(text)
    if len(text) <= limit:
        return text
    if keep == "tail":
        return "...[TRUNCATED]...\n" + text[-limit:]
    return text[:limit] + "\n...[TRUNCATED]..."


def _normalize_plan(raw_plan, result: dict, target_file: str) -> list:
    """
    Normalize the researcher's plan into steps the state manager can consume.
    """
    formatted = []
    if not isinstance(raw_plan, list):
        return formatted
    default_file = result.get("target_file") or target_file or ""

    for step in raw_plan:
        if isinstance(step, dict):
            title = str(step.get("title") or step.get("description") or "Execute task").strip()
            if not title:
                continue

            try:
                est_lines = int(step.get("estimated_lines", 0) or 0)
            except (TypeError, ValueError):
                est_lines = 0

            provides = step.get("provides") or []
            consumes = step.get("consumes") or []
            if not isinstance(provides, list):
                provides = []
            if not isinstance(consumes, list):
                consumes = []

            formatted.append({
                "title": title,
                "description": str(step.get("description", "")),
                "target_file": step.get("target_file") or default_file,
                "status": "pending",
                "estimated_lines": est_lines,
                "provides": [str(p) for p in provides],
                "consumes": [str(c) for c in consumes],
            })
        elif str(step).strip():
            formatted.append({
                "title": str(step).strip(),
                "description": "",
                "target_file": default_file,
                "status": "pending",
                "estimated_lines": 0,
                "provides": [],
                "consumes": [],
            })
    return formatted


def _build_legacy_lessons_context() -> str:
    return (
        f"=== PROMOTED LESSONS ===\n"
        f"{_clip(get_promoted_lessons_prompt(), LESSONS_BLOCK_CHAR_LIMIT)}\n\n"
        f"=== CANDIDATE LESSONS ===\n"
        f"{_clip(get_candidates_prompt(), LESSONS_BLOCK_CHAR_LIMIT)}\n\n"
        f"=== RAW OBSERVATIONS ===\n"
        f"{_clip(get_observations_prompt(), LESSONS_BLOCK_CHAR_LIMIT, keep='tail')}\n\n"
        f"=== DEPRECATED LESSONS ===\n"
        f"{_clip(get_deprecated_prompt(), LESSONS_BLOCK_CHAR_LIMIT)}"
    )


def run_researcher(
    state,
    project_name,
    project_dir=".",
    project_files="",
    session_context="",
):
    project_tree = get_project_file_tree(project_dir)
    if (not project_tree or project_tree == "Empty directory.") and project_files:
        project_tree = _clip(project_files, 4000)

    active_goal = state.get("goal") or state.get("task_prompt") or (
        (state.get("task_spec") or {}).get("raw_user_prompt", "")
    )
    active_subtask = state.get("active_subtask")
    target_file = state.get("target_file")
    existing_plan = state.get("plan", []) or []

    iteration_logs = (
        state.get("iteration_logs")
        or state.get("compiler_output")
        or "No logs recorded."
    )
    iteration_logs = _clip(iteration_logs, LOG_CHAR_LIMIT, keep="tail")

    plan_history = "\n".join([
        f"- [{str(step.get('status', 'pending')).upper()}] "
        f"{step.get('title')} (File: {step.get('target_file', 'N/A')})"
        for step in existing_plan if isinstance(step, dict)
    ]) if existing_plan else "None"

    if session_context:
        lessons_context = ""
    else:
        retrieval_query = " ".join(
            filter(None, [str(active_goal or ""), str(active_subtask or "")])
        ).strip() or "general research"

        try:
            _scored, lessons_context, lesson_node_ids = get_ranked_lessons_for_role(
                role="researcher",
                query=retrieval_query,
                limit=getattr(config, "LESSONS_RETRIEVAL_LIMIT", 5),
            )
        except Exception:
            lessons_context = ""
            lesson_node_ids = []

        if not lessons_context:
            lessons_context = _build_legacy_lessons_context()

        state["active_lesson_node_ids"] = list(lesson_node_ids or [])
        state["active_lessons_prompt"] = get_full_memory_prompt()

    skill_prompt = get_combined_agent_skill("researcher", project_dir)
    system_prompt = (
    f"{skill_prompt}\n\n"
    "You are the DifferiX Researcher. Your job is to analyze the goal and output a strict JSON execution plan.\n\n"
    "### PLANNING RULES\n"
    "1. Break the goal into atomic, sequential subtasks in `technical_plan`. Never return an empty plan.\n"
    "2. Only reference files existing in the PROJECT FILE TREE, or new files explicitly requested.\n"
    "3. Keep `analysis` to 1-2 short sentences.\n"
    "4. `estimated_lines`: Be conservative. If a single step produces >100 lines, SPLIT it into multiple "
    "sequential steps on the SAME target_file. Later steps will append to the file.\n"
    "5. `provides`/`consumes`: Use these to declare cross-file dependencies (e.g., [\"html:.hero-title\", \"file:script.js\"]).\n"
    "6. NEVER assign read-only tasks (e.g., 'Extract class names', 'Analyze structure') to the editor. "
    "The editor ONLY writes code. Do all analysis yourself and include the results directly in the step description.\n\n"
    "### WEB STYLES (If building a UI)\n"
    "Set `web_style` to EXACTLY one of these, or empty string if not a web task:\n"
    "- 'styles/style_3d_webgl' (space, gaming, 3D)\n"
    "- 'styles/style_clean_saas' (business, minimal)\n"
    "- 'styles/style_brutalist' (web3, raw, bold)\n"
    "- 'styles/style_cyberpunk' (hacker, neon)\n"
    "- 'styles/style_editorial' (blogs, text-heavy)\n\n"
    "### POLISH & RESPONSIVENESS RULE\n"
    "If the task is to build a website, you MUST append TWO final steps to the plan:\n"
    "   - Step N: 'Add CSS media queries for responsiveness and micro-interaction transitions (hover states, focus rings)' targeting 'styles.css'.\n"
    "   - Step N+1: 'Add JavaScript for custom cursor tracking, IntersectionObserver scroll-reveals, and dynamic element interactions' targeting 'script.js'.\n\n"
        "### GEO & AI-SEO RULE\n"
    "If the task is to build a website, you MUST also append these steps to the plan:\n"
    "   - Step N: 'Generate llms.txt in the project root with site summary and key links' targeting 'llms.txt'.\n"
    "   - Step N+1: 'Inject JSON-LD Schema.org (WebSite or Organization) into the <head> of index.html' targeting 'index.html'.\n\n"
    "### PERFORMANCE OPTIMIZATION RULE\n"
    "If the task is to build a website, you MUST append a FINAL step to the plan:\n"
    "   - Step N+2: 'Optimize performance: add defer to scripts, loading=\"lazy\" to images, font-display=swap to fonts, and ensure CSS animations use GPU transforms' targeting 'index.html' (and 'styles.css').\n\n"
    "### OUTPUT FORMAT\n"
    "Respond with ONLY a valid JSON object. Keys MUST be in this exact order:\n"
    "{\n"
    '  "analysis": "Short technical analysis.",\n'
    '  "web_style": "styles/style_clean_saas",\n'
    '  "target_file": "primary_file.py",\n'
    '  "technical_plan": [\n'
    "    {\n"
    '      "title": "Step description",\n'
    '      "target_file": "file.py",\n'
    '      "estimated_lines": 80,\n'
    '      "provides": ["html:.hero-title"],\n'
    '      "consumes": [],\n'
    '      "status": "pending"\n'
    "    }\n"
    "  ]\n"
        "7. MANDATORY FULL-STACK RULE: If the task is to build a website, your plan MUST include separate steps for: "
    "index.html, styles.css, script.js, llms.txt (GEO), and a final performance optimization pass. "
    "Never finish a plan until all these files are created or updated.\n\n"
    "}"
    )
    

    user_input_parts = []
    if session_context:
        user_input_parts.append(session_context)

    user_input_parts.append(
        f"=== OBJECTIVE ===\n"
        f"Goal: {active_goal}\n"
        f"Subtask: {active_subtask}\n"
        f"Target: {target_file}\n\n"
        f"=== PROGRESS / PLAN HISTORY ===\n{plan_history}\n\n"
        f"=== RECENT EXECUTION LOGS ===\n{iteration_logs}\n\n"
        f"=== PROJECT FILE TREE (project: {project_name}) ===\n{project_tree}"
    )

    if lessons_context:
        user_input_parts.append(f"=== LESSONS & CONTEXT ===\n{lessons_context}")

    user_input = "\n\n".join(user_input_parts)

    active_model = get_researcher_model()
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_input},
    ]

    result, result_text, provider, response = {}, "", None, None
    formatted_plan = []
    total_prompt_tokens = 0
    total_completion_tokens = 0
    failure_reason = ""

    for attempt, budget in enumerate(
        (RESEARCHER_MAX_TOKENS, RESEARCHER_RETRY_MAX_TOKENS)
    ):
        call_messages = list(messages)
        if attempt > 0:
            call_messages.append({
                "role": "user",
                "content": (
                    f"Your previous reply was unusable ({failure_reason}). "
                    f"Reply again with ONLY the JSON object, 'technical_plan' first, "
                    f"with at least one step, each step having an 'estimated_lines' "
                    f"integer, and a very short 'analysis'."
                ),
            })
        
        response, provider = safe_chat_completion(
            active_model,
            messages=call_messages,
            temperature=0.1,
            max_tokens=budget,
            task_spec=str(state.get("active_subtask") or state.get("goal") or "")
        )

        usage = getattr(response, "usage", None)
        total_prompt_tokens += getattr(usage, "prompt_tokens", 0) or 0
        total_completion_tokens += getattr(usage, "completion_tokens", 0) or 0

        choice = response.choices[0]
        finish_reason = getattr(choice, "finish_reason", None)
        result_text = (choice.message.content or "").strip()
        result = parse_researcher_payload(result_text)
        formatted_plan = _normalize_plan(
            result.get("technical_plan"), result, target_file
        )

        if formatted_plan:
            break

        if not result_text:
            failure_reason = f"empty content, finish_reason={finish_reason}"
        elif finish_reason == "length":
            failure_reason = "output truncated by token limit"
        elif result.get("_parse_failed"):
            failure_reason = "not valid JSON"
        else:
            failure_reason = "technical_plan was empty"
        print(
            f"[Researcher Warning] attempt {attempt + 1} gave no plan: "
            f"{failure_reason} (model={active_model}, max_tokens={budget})"
        )

    result.pop("_parse_failed", None)

    obs = result.get("observation")
    if obs and isinstance(obs, dict) and obs.get("root_cause"):
        obs_file = create_observation(
            root_cause=obs.get("root_cause"),
            details=obs.get("details", {}),
        )
        print(f"[Lesson Manager] Observation logged by Super-User: {obs_file}")

    existing_completed = [
        s for s in existing_plan
        if isinstance(s, dict) and s.get("status") == "completed"
    ]

    if formatted_plan:
        state["plan"] = existing_completed + formatted_plan
        state.pop("research_error", None)
    else:
        state["plan"] = existing_plan
        state["research_error"] = failure_reason or "researcher returned no plan"
        print(
            f"[Researcher Error] No plan produced after retry: "
            f"{state['research_error']}"
        )

    pending_steps = [
        s for s in state["plan"]
        if isinstance(s, dict) and s.get("status") == "pending"
    ]
    if pending_steps and pending_steps[0].get("target_file"):
        state["target_file"] = pending_steps[0]["target_file"]
    elif result.get("target_file"):
        state["target_file"] = str(result["target_file"]).strip()

    state["research_analysis"] = (
        result.get("analysis") or result_text[:1000] or "Executing subtask."
    )
    
    # --- SAVE WEB STYLE ---
    state["web_style"] = result.get("web_style", "")
    
    state["research_data"] = result
    state["current_step"] = "edit" if pending_steps else "research"

    usage_meta = type("Meta", (), {
        "prompt_token_count": total_prompt_tokens,
        "candidates_token_count": total_completion_tokens,
        "raw_response": result_text,
        "provider": provider,
        "model_id": active_model,
    })

    return state, usage_meta