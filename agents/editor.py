"""
Editor module for DifferiX framework.
Handles context assembly, LLM interaction for code editing, AST operations,
and diff application.
"""

import os
import json
import re
import ast
import config

from tools.lesson_manager import (
    get_ranked_lessons_for_role,
    get_promoted_lessons_prompt,
)
from tools.skill_manager import get_combined_agent_skill
import tools.ast_ops as ast_ops
import tools.tool_ops as tool_ops
import tools.ast_slicer as ast_slicer
import tools.code_tools as code_tools

try:
    from tools.llm_client import safe_chat_completion
except ImportError:
    from llm_client import safe_chat_completion


DIFFERIX_FRAMEWORK_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DIFFERIX_FRAMEWORK_CORE_FILES = {
    ".env", "config.py", "state.json", "deduplicate.py",
    "agents_chat.py", "agents_chat_2.py", "PROJECT_STRUCTURE.md",
}

# Diagnostic operations: produce output but MUST NOT modify the file
DIAGNOSTIC_OPERATIONS = {
    "check_syntax", "check_imports", "lint", "validate_syntax",
    "get_symbols", "extract_function", "extract_class",
    "py_compile", "run_snippet",
}


def get_editor_model() -> str:
    """Retrieve the active editor model from config with fallback support."""
    if hasattr(config, "get_active_model") and callable(getattr(config, "get_active_model")):
        return config.get_active_model("editor")
    if hasattr(config, "get_agent_model") and callable(getattr(config, "get_agent_model")):
        return config.get_agent_model("editor")
    return getattr(config, "MODEL_EDITOR", "anthropic/claude-3.7-sonnet")


def resolve_sandboxed_path(target_file: str, project_dir: str = ".") -> str:
    """Resolve and validate target file path against sandbox boundaries and core file locks."""
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


def _read_updated(target_path: str, fallback: str) -> str:
    """Safely read updated file content with fallback."""
    try:
        with open(target_path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return fallback


def dispatch_editor_tool(operation: str, payload: dict, project_dir: str = ".") -> dict:
    """
    Dispatch modular AST and tool operations.

    Payload for file-based ops MUST include 'file_path'.
    """
    if not isinstance(operation, str) or not operation.strip():
        return {
            "success": False, "operation": operation,
            "error": "operation must be a non-empty string",
        }
    if not isinstance(payload, dict):
        return {
            "success": False, "operation": operation,
            "error": "payload must be a dict",
        }

    op = operation.strip()

    try:
        # ---- AST read-only operations ----
        if op == "validate_syntax":
            res = ast_ops.validate_ast_syntax(
                payload.get("content", ""), payload.get("lang", "python")
            )
            return {
                "success": bool(res.get("valid")),
                "operation": op, "result": res, "error": res.get("error"),
            }

        if op == "get_symbols":
            res = ast_ops.get_available_symbols(
                payload.get("content", ""), payload.get("lang", "python")
            )
            return {
                "success": res.get("error") is None,
                "operation": op, "result": res, "error": res.get("error"),
            }

        if op == "extract_function":
            res = ast_ops.extract_function_source(
                payload.get("content", ""), payload.get("func_name", "")
            )
            return {
                "success": res.get("error") is None,
                "operation": op, "result": res, "error": res.get("error"),
            }

        if op == "extract_class":
            res = ast_ops.extract_class_source(
                payload.get("content", ""), payload.get("class_name", "")
            )
            return {
                "success": res.get("error") is None,
                "operation": op, "result": res, "error": res.get("error"),
            }

        # ---- File-based operations ----
        file_path = payload.get("file_path", "")
        if file_path and payload.get("sandbox", True):
            try:
                file_path = resolve_sandboxed_path(file_path, project_dir)
            except PermissionError as pe:
                return {"success": False, "operation": op, "error": str(pe)}

        if op == "format":
            res = code_tools.format_code(file_path)
            return {
                "success": res.get("ok", False),
                "operation": op, "result": res, "error": res.get("reason"),
            }

        if op == "lint":
            res = code_tools.lint_code(file_path)
            # lint always succeeds as an operation; errors are diagnostic output
            return {
                "success": True,
                "operation": op, "result": res, "error": None,
            }

        if op == "add_import":
            res = code_tools.smart_insert_import(
                file_path, payload.get("module", ""), payload.get("name", "")
            )
            return {
                "success": res.get("ok", False),
                "operation": op, "result": res, "error": res.get("reason"),
            }

        if op == "check_syntax":
            res = code_tools.run_syntax_check(file_path)
            return {
                "success": res.get("ok", False),
                "operation": op, "result": res, "error": res.get("reason"),
            }

        if op == "run_snippet":
            res = code_tools.run_snippet(
                payload.get("code", ""), timeout=payload.get("timeout", 2)
            )
            return {
                "success": res.get("ok", False),
                "operation": op, "result": res,
                "error": res.get("stderr") if not res.get("ok") else None,
            }

        if op == "check_imports":
            res = code_tools.check_imports(file_path)
            return {
                "success": True,  # diagnostic
                "operation": op, "result": res,
                "error": None if res.get("ok") else f"Missing: {res.get('missing', [])}",
            }

        if op == "add_dependency":
            res = code_tools.add_dependency(payload.get("package", ""))
            return {
                "success": res.get("ok", False),
                "operation": op, "result": res, "error": res.get("reason"),
            }

        if op == "insert_code":
            res = code_tools.insert_code_in_function(
                file_path,
                payload.get("function", ""),
                payload.get("anchor", ""),
                payload.get("code", ""),
            )
            return {
                "success": res.get("ok", False),
                "operation": op, "result": res, "error": res.get("reason"),
            }

        if op == "fix_syntax":
            res = code_tools.fix_unclosed_brackets(file_path)
            return {
                "success": res.get("ok", False),
                "operation": op, "result": res, "error": res.get("reason"),
            }

        if op == "idempotent_write":
            res = tool_ops.idempotent_write_file(
                file_path, payload.get("content", "")
            )
            return {
                "success": bool(res.get("success")),
                "operation": op, "result": res, "error": res.get("error"),
            }

        if op == "py_compile":
            res = tool_ops.run_py_compile_check(
                file_path, timeout=payload.get("timeout", 30)
            )
            return {
                "success": True,  # diagnostic
                "operation": op, "result": res, "error": res.get("error"),
            }

        return {
            "success": False, "operation": op,
            "error": f"Unsupported tool operation: {op}",
        }

    except Exception as exc:
        return {"success": False, "operation": op, "error": str(exc)}


def apply_diff_blocks(
    original_content: str,
    diff_text: str,
    allow_full_rewrite: bool = False,
    target_file: str = "",
    target_path: str = "",
) -> dict:
    """Applies structured AST operations, tool calls, or fallback search/replace diffs."""
    diff_text = diff_text.replace("\r\n", "\n")
    original_content = original_content.replace("\r\n", "\n")

    json_str = ast_ops.extract_json_object(diff_text)
    if json_str:
        try:
            op_data = json.loads(json_str)
        except json.JSONDecodeError as e:
            return {
                "ok": False, "code": original_content, "matched": 0,
                "failed": [{"reason": f"JSON_PARSE_ERROR: {e}"}], "mode": "rejected",
            }

        op_type = op_data.get("operation")
        lang = "python" if target_file.endswith(".py") else "javascript"
        tpath = target_path or os.path.join(os.getcwd(), target_file)

        try:
            # ---- AST deterministic mutations ----
            if op_type == "replace_function":
                modifier = ast_ops.ReplaceFunction(
                    op_data["name"], op_data["new_body"]
                )
                new_content = modifier.apply(original_content, lang)
                return {
                    "ok": True, "code": new_content, "matched": 1,
                    "failed": [], "mode": "ast_replace_function",
                }

            elif op_type == "replace_class":
                modifier = ast_ops.ReplaceClass(
                    op_data["name"], op_data["new_content"]
                )
                new_content = modifier.apply(original_content, lang)
                return {
                    "ok": True, "code": new_content, "matched": 1,
                    "failed": [], "mode": "ast_replace_class",
                }

            elif op_type == "insert_after":
                modifier = ast_ops.InsertAfter(
                    op_data["anchor"], op_data["code"]
                )
                new_content = modifier.apply(original_content, lang)
                return {
                    "ok": True, "code": new_content, "matched": 1,
                    "failed": [], "mode": "ast_insert_after",
                }

            # ---- Tool-level operations ----
            elif op_type in (
                "fix_syntax", "check_syntax", "run_snippet", "check_imports",
                "add_dependency", "insert_code", "format", "lint", "add_import",
                "idempotent_write", "py_compile", "validate_syntax",
                "get_symbols", "extract_function", "extract_class",
            ):
                if op_type not in (
                    "run_snippet", "add_dependency", "validate_syntax",
                    "get_symbols", "extract_function", "extract_class",
                ):
                    op_data["file_path"] = tpath

                dispatch_res = dispatch_editor_tool(
                    op_type, op_data, os.path.dirname(tpath) or "."
                )

                # ---- Diagnostic operations: don't touch disk ----
                if op_type in DIAGNOSTIC_OPERATIONS:
                    diagnostic = dispatch_res.get("result", {})
                    return {
                        "ok": True,
                        "code": original_content,
                        "matched": 0,
                        "failed": [],
                        "mode": f"diagnostic_{op_type}",
                        "diagnostic": diagnostic,
                    }

                if not dispatch_res.get("success"):
                    return {
                        "ok": False, "code": original_content, "matched": 0,
                        "failed": [{
                            "reason": dispatch_res.get(
                                "error", f"{op_type} failed"
                            )
                        }],
                        "mode": f"tool_{op_type}",
                    }

                if op_type in (
                    "fix_syntax", "insert_code", "format", "lint",
                    "add_import", "idempotent_write",
                ):
                    new_content = _read_updated(tpath, original_content)
                else:
                    new_content = original_content

                return {
                    "ok": True, "code": new_content, "matched": 1,
                    "failed": [], "mode": f"tool_{op_type}",
                }

            else:
                return {
                    "ok": False, "code": original_content, "matched": 0,
                    "failed": [{"reason": f"UNKNOWN_OPERATION: '{op_type}'"}],
                    "mode": "rejected",
                }

        except Exception as e:
            return {
                "ok": False, "code": original_content, "matched": 0,
                "failed": [{"reason": f"AST_OPERATION_FAILED: {str(e)}"}],
                "mode": "rejected",
            }

    is_empty_file = not original_content.strip()
    has_code_fence = bool(re.search(r"```[a-zA-Z]*\n.*?```", diff_text, re.DOTALL))

    full_rewrite_intent = (
        is_empty_file
        or allow_full_rewrite
        or has_code_fence
        or ast_ops.looks_like_full_rewrite(diff_text)
    )

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
        return {
            "ok": True, "code": "\n".join(clean_lines), "matched": 0,
            "failed": [], "mode": "full_rewrite",
        }

    pattern = r"<{4,}\s*SEARCH[ \t]*\n(.*?)\n?={4,}[ \t]*\n(.*?)\n?>{4,}\s*REPLACE"
    matches = re.findall(pattern, diff_text, re.DOTALL)

    if not matches:
        return {
            "ok": False, "code": original_content, "matched": 0,
            "failed": [{"reason": "NO_SEARCH_BLOCKS"}], "mode": "rejected",
        }

    updated_content = original_content
    failed_blocks = []
    matched_count = 0

    for idx, (search_block, replace_block) in enumerate(matches, 1):
        start, end = ast_ops.flexible_find(updated_content, search_block)
        if start is not None:
            updated_content = (
                updated_content[:start] + replace_block + updated_content[end:]
            )
            matched_count += 1
        else:
            failed_blocks.append({
                "block_index": idx,
                "search_preview": search_block[:150],
                "reason": "NOT_FOUND",
            })

    if matched_count == 0:
        return {
            "ok": False, "code": original_content, "matched": 0,
            "failed": failed_blocks, "mode": "rejected",
        }

    return {
        "ok": True, "code": updated_content, "matched": matched_count,
        "failed": failed_blocks,
        "mode": "search_replace" if not failed_blocks else "partial_search_replace",
    }


def run_editor(
    state: dict,
    project_files: str = "",
    project_dir: str = ".",
    session_context: str = "",
    related_context: str = "",
) -> tuple:
    """Execute the editor agent workflow."""
    from tools.context_assembler import (
        ContextBundle, Tier, TierItem, ContextAssembler,
        smart_slice_code, dedupe_logs,
    )

    target_file = state.get("target_file") or "main.py"
    safe_target_path = resolve_sandboxed_path(target_file, project_dir)

    target_code = ""
    file_hash = None
    if os.path.exists(safe_target_path):
        try:
            freshness = ast_slicer.verify_context_freshness(safe_target_path)
            if isinstance(freshness, dict):
                file_hash = freshness.get("hash")
            with open(safe_target_path, "r", encoding="utf-8") as f:
                target_code = f.read()
        except Exception as e:
            target_code = f"# Error reading file: {e}"

    state["_expected_file_hash"] = file_hash

    iteration_logs = state.get("iteration_logs") or state.get("compiler_output", "") or ""
    iteration_number = state.get("iteration_count", 0)

    lessons_block = ""
    if not session_context:
        retrieval_query = str(state.get("active_subtask") or state.get("goal") or target_file)
        _scored, lessons_block, lesson_node_ids = get_ranked_lessons_for_role(
            role="editor", query=retrieval_query,
            limit=getattr(config, "LESSONS_RETRIEVAL_LIMIT", 5),
        )
        if not lessons_block:
            lessons_block = get_promoted_lessons_prompt()
        state["active_lesson_node_ids"] = list(lesson_node_ids or [])

    _raw_styles = state.get("web_style", "")
    active_styles = (
        [_raw_styles] if isinstance(_raw_styles, str) and _raw_styles
        else (list(_raw_styles) if isinstance(_raw_styles, list) else [])
    )

    if target_file.endswith((".html", ".css", ".js")) and "styles/style_polish" not in active_styles:
        active_styles.append("styles/style_polish")

    try:
        system_prompt = get_combined_agent_skill(
            "editor", project_dir, active_styles=active_styles
        )
    except TypeError:
        system_prompt = get_combined_agent_skill("editor", project_dir)

    editor_feedback = state.pop("editor_feedback", None)
    editor_payload = {
        "target_file": target_file,
        "goal": state.get("goal", ""),
        "subtask": state.get("active_subtask", ""),
        "research": state.get("research_analysis") or "",
        "style_requirement": ", ".join(active_styles) or "default",
    }
    if editor_feedback:
        editor_payload["REJECTION_FEEDBACK"] = editor_feedback

    is_new_or_empty = not target_code.strip()
    file_mode_instruction = (
        "FILE STATUS: NEW OR EMPTY FILE.\n"
        "INSTRUCTION: Output the COMPLETE file code inside a single standard Markdown code block."
        if is_new_or_empty else
        "FILE STATUS: EXISTING FILE.\n"
        "INSTRUCTION: Use JSON AST Modification or SEARCH/REPLACE blocks."
    )

    bundle = ContextBundle()
    bundle.add_text(Tier.T0_STATIC, "skills", system_prompt)
    bundle.add_text(
        Tier.T1_ACTION_CORE,
        "spec",
        f"=== FILE WRITING MODE ===\n{file_mode_instruction}\n\n"
        f"=== SPECIFICATION ===\n{json.dumps(editor_payload, ensure_ascii=False)}",
    )

    if session_context:
        bundle.add_text(Tier.T1_ACTION_CORE, "session", session_context)

    # ---- Phase 1.1 fix: inject full function source when replace_function is expected ----
    _subtask_lower = str(state.get("active_subtask") or "").lower()
    if "replace_function" in _subtask_lower and target_file.endswith(".py") and target_code:
        _m = re.search(r"function[\s:]+['\"]?(\w+)", state.get("active_subtask") or "")
        if _m:
            _func_name = _m.group(1)
            _extract_res = ast_ops.extract_function_source(target_code, _func_name)
            if _extract_res.get("source"):
                bundle.add_text(
                    Tier.T1_ACTION_CORE,
                    "full_function_under_edit",
                    f"=== FULL SOURCE OF '{_func_name}' (you MUST preserve ALL this logic) ===\n"
                    f"```python\n{_extract_res['source']}\n```"
                )

    if target_code:
        if is_new_or_empty:
            bundle.add_text(
                Tier.T2_TARGET_SLICE, "target",
                f"=== TARGET FILE ({target_file}) ===\n[empty]",
            )
        else:
            hints = str(state.get("active_subtask") or "")
            if target_file.endswith(".py"):
                sliced = ast_slicer.smart_ast_slice(
                    target_code, target_file, max_lines=150, hints=hints
                )
            else:
                sliced = smart_slice_code(
                    target_code, target_file, max_lines=150
                )
            bundle.add(TierItem(
                tier=Tier.T2_TARGET_SLICE, key="target",
                content=f"=== TARGET FILE ({target_file}) ===\n{sliced}",
            ))

    if related_context:
        bundle.add_text(Tier.T3_INTERFACE, "related", related_context)
    if lessons_block:
        bundle.add_text(Tier.T4_ENRICHMENT, "lessons", lessons_block)

    if iteration_logs.strip() and (
        iteration_number > 0
        or getattr(config, "TCA_INCLUDE_FEEDBACK_ON_FIRST_CYCLE", False)
    ):
        deduped = dedupe_logs(iteration_logs, max_errors=5)
        if deduped:
            bundle.add_text(
                Tier.T5_FEEDBACK, "logs",
                f"=== RECENT ERRORS ===\n{deduped}", optional=True,
            )

    assembled = ContextAssembler(
        budget=getattr(config, "TOKEN_BUDGET_EDITOR", 18000),
        safety_margin=getattr(config, "TCA_SAFETY_MARGIN", 0.9),
    ).assemble(bundle)

    active_model = get_editor_model()
    response, provider = safe_chat_completion(
        active_model,
        messages=[
            {"role": "system", "content": assembled.system_prompt},
            {"role": "user", "content": assembled.user_prompt},
        ],
        temperature=0.15,
        max_tokens=10000,
        extra_body={"reasoning": {"max_tokens": 8192}},
    )

    if not response or not getattr(response, "choices", None) or len(response.choices) == 0:
        raise RuntimeError(f"Error: Editor received no choices ({active_model}).")

    choice = response.choices[0]
    message = getattr(choice, "message", None)
    result_text = (
        getattr(message, "content", "")
        or getattr(message, "reasoning_content", "")
        or ""
    ).strip()

    if not result_text:
        raise RuntimeError("Error: Editor received empty response.")

    target_file_match = re.search(r"TARGET_FILE:\s*([^\n]+)", result_text)
    if target_file_match:
        target_file = re.sub(r"[`'\"]", "", target_file_match.group(1).strip())
        safe_target_path = resolve_sandboxed_path(target_file, project_dir)
        if os.path.exists(safe_target_path):
            try:
                with open(safe_target_path, "r", encoding="utf-8") as f:
                    target_code = f.read()
            except Exception:
                pass

    if "=======" in result_text and "<<<<<<<" not in result_text:
        result_text = result_text.replace("=======", "")

    apply_res = apply_diff_blocks(
        target_code, result_text,
        allow_full_rewrite=state.get("allow_full_rewrite", False),
        target_file=target_file,
        target_path=safe_target_path,
    )

    state["generated_code"] = {
        "target_file": target_file,
        "action": "modify",
        "code": apply_res["code"],
        "apply_result": apply_res,
    }

    if apply_res.get("mode") == "partial_search_replace" and apply_res.get("failed"):
        failed_previews = "; ".join(
            f"block {f.get('block_index')}: {f.get('reason', '')[:120]}"
            for f in apply_res["failed"][:3]
        )
        state["editor_feedback"] = (
            f"PARTIAL APPLY: {apply_res.get('matched', 0)} applied, "
            f"{len(apply_res['failed'])} failed.\n{failed_previews}"
        )

    usage = getattr(response, "usage", None)
    usage_meta = type("Meta", (), {
        "prompt_token_count": getattr(usage, "prompt_tokens", 0) or 0,
        "candidates_token_count": getattr(usage, "completion_tokens", 0) or 0,
        "raw_response": result_text,
        "provider": provider,
        "model_id": active_model,
    })

    return state, usage_meta