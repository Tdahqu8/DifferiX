import json
from openai import OpenAI
import config

client = OpenAI(
    base_url=config.OPENROUTER_BASE_URL,
    api_key=config.OPENROUTER_API_KEY,
)

def run_reviewer(state):
    system_prompt = """You are the AI Critic (Reviewer) in the f(x) system.

Your task: verify the output of the Editor based STRICTLY on facts.

You are provided with:
1. compiler_output / test_output — actual execution log (build or tests)
2. generated_code — what the Editor modified (target_file, action, code)
3. research_data / plan — initial plan
4. project_structure — current project structure
5. existing warnings / lessons (if any)

STRICT RULES:
- You MUST NOT set is_approved=true if compiler_output/test_output contains errors, traceback, FAILED, error CS, Exception.
- Do NOT approve code based on guesswork. The sole source of truth is the execution log.
- If execution logs are missing, set is_approved=false and request the orchestrator to run the check.
- If dangerous operations are present in the code (file deletion, shutil.rmtree, os.remove, pathlib.unlink, rm -rf, etc.), set is_approved=false.
- Specify file paths relative to the project root, matching project_structure.

RESPONSE FORMAT — STRICTLY JSON:
{
    "is_approved": false,
    "verdict": "short verdict",
    "root_cause": "why it broke / what went wrong (1-3 sentences)",
    "fix_instruction": "specific instructions for the Editor on what to fix",
    "dangerous_code_found": false,
    "should_create_observation": true,
    "observation_draft": {
        "rule": "short rule for the future",
        "why": "why this is important",
        "tags": ["compiler", "path", "..."]
    },
    "reasoning_summary": "how you reached your conclusion (2-5 sentences)"
}

If is_approved=true:
- fix_instruction can be empty
- should_create_observation is usually false
- reasoning_summary is still mandatory
CRITICAL RULE ON TASK COMPLETION:
- You are approving ONLY the current file edit, NOT the entire project goal.
- Never set current_step to "done" in your reasoning. Only approve or reject the code change itself.
- Prohibited operations: direct file deletions using os.remove or shutil.rmtree"""

    # 1. Prune context to prevent overloading model context window
    compiler_log = str(state.get("compiler_output", ""))
    pruned_state = {
        "iteration_count": state.get("iteration_count", 0),
        "compiler_output": compiler_log[-3000:] if len(compiler_log) > 3000 else compiler_log,  # take tail of log
        "generated_code": state.get("generated_code"),
        "research_data": state.get("research_data"),
        "warnings": state.get("warnings", [])
    }

    # 2. API call with guaranteed token margin
    response = client.chat.completions.create(
        model=config.MODEL_REVIEWER,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"State: {json.dumps(pruned_state, ensure_ascii=False)}"}
        ],
        response_format={"type": "json_object"},
        temperature=0.1,
        max_tokens=2048
    )
    
    # 3. Validate API response
    if not response or not getattr(response, "choices", None) or len(response.choices) == 0:
        raise RuntimeError("Reviewer: model returned an empty response or response.choices == None")

    first_choice = response.choices[0]
    if not first_choice or not getattr(first_choice, "message", None):
        raise RuntimeError("Reviewer: missing message in model response")

    result_text = (first_choice.message.content or "").strip()
    if not result_text:
        raise RuntimeError("Reviewer: model returned empty content string")

    # 4. Safe JSON parsing with recovery (json_repair) and fallback
    result = None
    try:
        result = json.loads(result_text)
    except json.JSONDecodeError:
        try:
            import json_repair
            result = json_repair.loads(result_text)
            print("[Reviewer]: Invalid JSON detected, successfully repaired via json_repair.")
        except Exception:
            print("[Reviewer]: Model truncated response. Performing soft rollback to edit step.")
            result = {
                "is_approved": False,
                "verdict": "Reviewer response format error (JSON was truncated)",
                "root_cause": "Model exceeded generation limit or truncated response mid-stream.",
                "fix_instruction": "Parsing error occurred for review response. Retry or check file syntax."
            }

    # 5. Process verdict and route state
    if not result.get("is_approved", False):
        fix_instruction = result.get("fix_instruction", "")

        if isinstance(state.get("research_data"), dict):
            state["research_data"]["reviewer_fix"] = fix_instruction
        elif isinstance(state.get("research_data"), str):
            state["research_data"] += f"\n[Reviewer Fix]: {fix_instruction}"
        else:
            state["research_data"] = f"[Reviewer Fix]: {fix_instruction}"

        state["review_result"] = result
        state["current_step"] = "edit"
        state["iteration_count"] = state.get("iteration_count", 0) + 1
    else:
        state["review_result"] = result
        state["current_step"] = "done"
        
    # 6. Extract token usage
    usage = getattr(response, "usage", None)
    prompt_tokens = getattr(usage, "prompt_tokens", 0) if usage else 0
    completion_tokens = getattr(usage, "completion_tokens", 0) if usage else 0

    usage_meta = type('Meta', (), {
        'prompt_token_count': prompt_tokens,
        'candidates_token_count': completion_tokens
    })
    
    return state, usage_meta