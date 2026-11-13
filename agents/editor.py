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


class ModificationPrimitive:
    """Base class for structured modification operations"""
    
    @staticmethod
    def validate_target(content: str, target: str) -> bool:
        """Validate that target exists in content"""
        return target in content
    
    @staticmethod
    def get_available_symbols(content: str, lang: str) -> list:
        """Get available symbols in content based on language"""
        if lang == "python":
            import ast
            try:
                tree = ast.parse(content)
                symbols = []
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)):
                        # Get full function/class source including decorators
                        start_line = node.lineno - 1
                        end_line = getattr(node, 'end_lineno', node.lineno)
                        
                        # Handle decorators
                        if node.decorator_list:
                            start_line = min(d.lineno - 1 for d in node.decorator_list)
                        
                        # Get source lines
                        source_lines = content.splitlines()[start_line:end_line]
                        full_source = '\n'.join(source_lines)
                        
                        symbols.append({
                            "name": node.name,
                            "type": type(node).__name__,
                            "lineno": node.lineno,
                            "end_lineno": end_line,
                            "col_offset": node.col_offset,
                            "end_col_offset": getattr(node, 'end_col_offset', 0),
                            "source": full_source,
                            "has_decorators": bool(node.decorator_list)
                        })
                return symbols
            except SyntaxError:
                return []
        elif lang == "javascript":
            # Enhanced JS function/class extraction with source capture
            symbols = []
            lines = content.splitlines()
            for match in re.finditer(r"(function\s+(\w+)\s*\()|(class\s+(\w+)\s*{)", content):
                start_line = content.count('\n', 0, match.start()) + 1
                if match.group(2):  # Function match
                    # Find function end by matching braces
                    brace_level = 0
                    pos = match.end()
                    while pos < len(content):
                        if content[pos] == '{':
                            brace_level += 1
                        elif content[pos] == '}':
                            brace_level -= 1
                            if brace_level == 0:
                                break
                        pos += 1
                    end_line = content.count('\n', 0, pos) + 1
                    source = '\n'.join(lines[start_line-1:end_line])
                    
                    symbols.append({
                        "name": match.group(2),
                        "type": "FunctionDef",
                        "lineno": start_line,
                        "end_lineno": end_line,
                        "source": source
                    })
                else:  # Class match
                    # Find class end by matching braces
                    brace_level = 0
                    pos = match.end()
                    while pos < len(content):
                        if content[pos] == '{':
                            brace_level += 1
                        elif content[pos] == '}':
                            brace_level -= 1
                            if brace_level == 0:
                                break
                        pos += 1
                    end_line = content.count('\n', 0, pos) + 1
                    source = '\n'.join(lines[start_line-1:end_line])
                    
                    symbols.append({
                        "name": match.group(4),
                        "type": "ClassDef",
                        "lineno": start_line,
                        "end_lineno": end_line,
                        "source": source
                    })
            return symbols
        return []

class ReplaceFunction(ModificationPrimitive):
    """Replace a function definition with new content"""
    
    def __init__(self, name: str, new_body: str):
        self.name = name
        self.new_body = new_body
        
    def apply(self, content: str, lang: str) -> str:
        if lang == "python":
            import ast
            try:
                tree = ast.parse(content)
                lines = content.splitlines()
                modified = False
                
                for node in ast.walk(tree):
                    if isinstance(node, ast.FunctionDef) and node.name == self.name:
                        # Validate the new function body is syntactically valid and maintains scope
                        try:
                            new_func = ast.parse(self.new_body)
                            if not isinstance(new_func, ast.Module) or len(new_func.body) != 1:
                                raise ValueError(
                                    f"Function body for {self.name} must contain exactly one function definition"
                                )
                            if not isinstance(new_func.body[0], ast.FunctionDef):
                                raise ValueError(
                                    f"Function body for {self.name} must be a function definition"
                                )
                            
                            # Validate indentation matches original
                            first_line = self.new_body.splitlines()[0] if self.new_body else ""
                            if first_line and not first_line[0].isspace() and node.col_offset > 0:
                                raise ValueError(
                                    f"Function {self.name} must maintain original indentation level"
                                )
                        except SyntaxError as e:
                            raise ValueError(
                                f"Invalid function body for {self.name}: {str(e)}"
                            ) from e
                        
                        # Get the exact source lines including decorators
                        start_line = node.lineno - 1
                        end_line = node.end_lineno
                        
                        # Handle decorators
                        if node.decorator_list:
                            start_line = min(d.lineno - 1 for d in node.decorator_list)
                        
                        # Reconstruct with proper indentation
                        indent = ' ' * node.col_offset
                        new_body = [indent + line for line in self.new_body.splitlines()]
                        
                        lines = lines[:start_line] + new_body + lines[end_line:]
                        modified = True
                        break
                
                if not modified:
                    available = self.get_available_symbols(content, lang)
                    raise ValueError(
                        f"Function '{self.name}' not found. Available functions: {available}"
                    )
                
                return "\n".join(lines)
            except SyntaxError as e:
                raise ValueError(f"Target file syntax error: {str(e)}") from e
        return content

class ReplaceClass(ModificationPrimitive):
    """Replace a class definition with new content"""
    
    def __init__(self, name: str, new_content: str):
        self.name = name
        self.new_content = new_content
        
    def apply(self, content: str, lang: str) -> str:
        if lang == "python":
            import ast
            try:
                tree = ast.parse(content)
                lines = content.splitlines()
                modified = False
                
                for node in ast.walk(tree):
                    if isinstance(node, ast.ClassDef) and node.name == self.name:
                        # Get the exact source lines including decorators
                        start_line = node.lineno - 1
                        end_line = node.end_lineno
                        
                        # Handle decorators
                        if node.decorator_list:
                            start_line = min(d.lineno - 1 for d in node.decorator_list)
                        
                        # Reconstruct with proper indentation
                        indent = ' ' * node.col_offset
                        new_content = [indent + line for line in self.new_content.splitlines()]
                        
                        lines = lines[:start_line] + new_content + lines[end_line:]
                        modified = True
                        break
                
                return "\n".join(lines) if modified else content
            except SyntaxError:
                pass
        return content

class InsertAfter(ModificationPrimitive):
    """Insert code after a specific anchor"""
    
    def __init__(self, anchor: str, code: str):
        self.anchor = anchor
        self.code = code
        
    def apply(self, content: str, lang: str) -> str:
        if self.anchor in content:
            parts = content.split(self.anchor)
            if len(parts) > 1:
                return parts[0] + self.anchor + "\n" + self.code + parts[1]
        return content


def _flexible_find(content: str, search_block: str) -> tuple:
    """Find search_block in content, falling back to fuzzy matching if exact match fails."""
    if search_block in content:
        start = content.find(search_block)
        return start, start + len(search_block)
    
    search_lines_stripped = "\n".join(line.rstrip() for line in search_block.splitlines())
    content_lines_stripped = "\n".join(line.rstrip() for line in content.splitlines())
    
    if search_lines_stripped and content_lines_stripped.count(search_lines_stripped) == 1:
        start = content_lines_stripped.find(search_lines_stripped)
        return start, start + len(search_lines_stripped)
        
    import difflib
    search_lines = search_block.splitlines()
    content_lines = content.splitlines()
    
    best_ratio = 0.0
    best_start = None
    best_end = None
    
    if len(search_lines) > 0 and len(content_lines) >= len(search_lines):
        for i in range(len(content_lines) - len(search_lines) + 1):
            window = content_lines[i:i + len(search_lines)]
            window_str = "\n".join(window)
            ratio = difflib.SequenceMatcher(None, search_lines_stripped, window_str).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                # Calculate char indices
                best_start = sum(len(line) + 1 for line in content_lines[:i])
                best_end = best_start + len(window_str)

    if best_ratio >= 0.85 and best_start is not None:
        return best_start, best_end
        
    return None, None


def _looks_like_full_rewrite(diff_text: str) -> bool:
    """Detect a response that intends to be a full-file rewrite."""
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


def apply_diff_blocks(
    original_content: str,
    diff_text: str,
    allow_full_rewrite: bool = False,
    target_file: str = "",
) -> dict:
    """Applies SEARCH/REPLACE diff blocks with resilient matching and partial apply."""
    diff_text = diff_text.replace("\r\n", "\n")
    original_content = original_content.replace("\r\n", "\n")

    # Check for JSON-based AST modification
    json_match = re.search(r"```(?:json)?\s*(\{.*?\"operation\".*?\})\s*```", diff_text, re.DOTALL)
    if not json_match:
        json_match = re.search(r"(\{.*?\"operation\".*?\})", diff_text, re.DOTALL)
        
    if json_match:
        try:
            op_data = json.loads(json_match.group(1))
            op_type = op_data.get("operation")
            lang = "python" if target_file.endswith(".py") else "javascript"
            
            if op_type == "replace_function":
                modifier = ReplaceFunction(op_data["name"], op_data["new_body"])
                new_content = modifier.apply(original_content, lang)
                return {"ok": True, "code": new_content, "matched": 1, "failed": [], "mode": "ast_replace_function"}
                
            elif op_type == "replace_class":
                modifier = ReplaceClass(op_data["name"], op_data["new_content"])
                new_content = modifier.apply(original_content, lang)
                return {"ok": True, "code": new_content, "matched": 1, "failed": [], "mode": "ast_replace_class"}
                
            elif op_type == "insert_after":
                modifier = InsertAfter(op_data["anchor"], op_data["code"])
                new_content = modifier.apply(original_content, lang)
                return {"ok": True, "code": new_content, "matched": 1, "failed": [], "mode": "ast_insert_after"}
                
        except Exception as e:
            return {
                "ok": False, "code": original_content, "matched": 0,
                "failed": [{"reason": f"AST_OPERATION_FAILED: {str(e)}"}],
                "mode": "rejected"
            }

    is_empty_file = not original_content.strip()

    # HARD RULE: Never allow full rewrite of an existing HTML file.
    if target_file.endswith((".html", ".htm")) and len(original_content) > 200:
        allow_full_rewrite = False

    full_rewrite_intent = is_empty_file or allow_full_rewrite or _looks_like_full_rewrite(diff_text)

    # Mode A: full rewrite
    if full_rewrite_intent:
        code_match = re.search(
            r"```(?:python|csharp|cs|luau|lua|xml|json|html|css|js|ts|jsx|tsx|yaml|yml|sql|sh|bash|shell)?\n(.*?)```",
            diff_text,
            re.DOTALL,
        )
        candidate = code_match.group(1).strip() if code_match else diff_text.strip()
        candidate = re.sub(r"^TARGET_FILE:\s*[^\n]+\n?", "", candidate).strip()

        clean_lines = [
            line
            for line in candidate.splitlines()
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
                    "failed": [{
                        "search_preview": "JSON_VALIDATION_FAILED",
                        "reason": "Candidate output is invalid JSON.",
                    }],
                    "mode": "rejected",
                }

        # JS syntax guard for full rewrite
        if target_file.endswith((".js", ".mjs", ".cjs")):
            import tempfile
            import subprocess

            tmp_path = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w", suffix=".js", delete=False, encoding="utf-8"
                ) as tmp:
                    tmp.write(candidate_code)
                    tmp_path = tmp.name
                check = subprocess.run(
                    ["node", "--check", tmp_path],
                    capture_output=True,
                    text=True,
                    timeout=12,
                )
                if check.returncode != 0:
                    err = (check.stderr or check.stdout or "").strip()[:500]
                    return {
                        "ok": False,
                        "code": original_content,
                        "matched": 0,
                        "failed": [{
                            "search_preview": "JS_SYNTAX_ERROR",
                            "reason": f"JS_SYNTAX_ERROR after full rewrite: {err}",
                        }],
                        "mode": "rejected",
                    }
            except FileNotFoundError:
                pass
            except Exception:
                pass
            finally:
                if tmp_path and os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except OSError:
                        pass

        return {
            "ok": True,
            "code": candidate_code,
            "matched": 0,
            "failed": [],
            "mode": "full_rewrite",
        }

    # Mode B: SEARCH/REPLACE
    pattern = r"<{4,}\s*SEARCH[ \t]*\n(.*?)\n?={4,}[ \t]*\n(.*?)\n?>{4,}\s*REPLACE"
    matches = re.findall(pattern, diff_text, re.DOTALL)

    if not matches:
        return {
            "ok": False,
            "code": original_content,
            "matched": 0,
            "failed": [{
                "search_preview": "NO_SEARCH_BLOCKS",
                "reason": "No SEARCH/REPLACE blocks found in patch for non-empty file.",
            }],
            "mode": "rejected",
        }

    updated_content = original_content
    failed_blocks = []
    matched_count = 0

    for idx, (search_block, replace_block) in enumerate(matches, 1):
        start, end = _flexible_find(updated_content, search_block)

        if start is not None:
            updated_content = updated_content[:start] + replace_block + updated_content[end:]
            matched_count += 1
        else:
            tail_preview = "\n".join(updated_content.splitlines()[-8:])
            failed_blocks.append({
                "block_index": idx,
                "search_preview": search_block[:180] + ("..." if len(search_block) > 180 else ""),
                "reason": (
                    "NOT_FOUND: SEARCH block does not match current file content. "
                    "Copy the anchor verbatim from the TARGET FILE view. "
                    f"Current file tail:\n{tail_preview}"
                ),
            })

    # Partial success: keep applied blocks if at least one matched
    if matched_count == 0:
        return {
            "ok": False,
            "code": original_content,
            "matched": 0,
            "failed": failed_blocks,
            "mode": "rejected",
        }

    # JS syntax guard on the result
    if target_file.endswith((".js", ".mjs", ".cjs")):
        import tempfile
        import subprocess

        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".js", delete=False, encoding="utf-8"
            ) as tmp:
                tmp.write(updated_content)
                tmp_path = tmp.name
            check = subprocess.run(
                ["node", "--check", tmp_path],
                capture_output=True,
                text=True,
                timeout=12,
            )
            if check.returncode != 0:
                err = (check.stderr or check.stdout or "").strip()[:500]
                return {
                    "ok": False,
                    "code": original_content,
                    "matched": matched_count,
                    "failed": failed_blocks + [{
                        "search_preview": "JS_SYNTAX_ERROR",
                        "reason": f"JS_SYNTAX_ERROR after apply: {err}",
                    }],
                    "mode": "rejected",
                }
        except FileNotFoundError:
            pass
        except Exception:
            pass
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

    return {
        "ok": True,
        "code": updated_content,
        "matched": matched_count,
        "failed": failed_blocks,
        "mode": "search_replace" if not failed_blocks else "partial_search_replace",
    }


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

    if any(x.endswith((".html", ".css", ".js")) for x in [target_file]):
        if "styles/style_polish" not in active_styles:
            active_styles.append("styles/style_polish")

    try:
        system_prompt = get_combined_agent_skill("editor", project_dir, active_styles=active_styles)
    except TypeError:
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

    # T1: action core
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

            # JS/TS files are dense — give them more room
            is_js = target_file.endswith((".js", ".mjs", ".cjs", ".ts", ".tsx"))
            total_lines = len(target_code.splitlines())
            if is_js:
                max_normal = 900 if total_lines < 900 else 500
                max_tight = 300 if total_lines < 900 else 180
            else:
                max_normal = 150
                max_tight = 50

            sliced = smart_slice_code(
                target_code, target_file, max_lines=max_normal, hints=hints
            )
            tightly = smart_slice_code(
                target_code, target_file, max_lines=max_tight, hints=hints
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

    # T3: related files
    if related_context:
        bundle.add_text(Tier.T3_INTERFACE, "related", related_context)

    # T4: lessons
    if lessons_block:
        bundle.add_text(Tier.T4_ENRICHMENT, "lessons", lessons_block)

    # T5: deduped logs
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
        max_tokens=10000,
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

    # Reload target_code if the model requested a different file
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

    # Sanitize hallucinated diff markers in full rewrites
    if "=======" in result_text and "<<<<<<<" not in result_text:
        result_text = result_text.replace("=======", "")
        result_text = re.sub(r"={4,}", "", result_text)

    # Apply against FULL file
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

    # Surface partial-apply info so the next iteration can finish the job
    if apply_res.get("mode") == "partial_search_replace" and apply_res.get("failed"):
        failed_previews = "; ".join(
            f"block {f.get('block_index')}: {f.get('reason', '')[:120]}"
            for f in apply_res["failed"][:3]
        )
        state["editor_feedback"] = (
            f"PARTIAL APPLY: {apply_res.get('matched', 0)} block(s) applied, "
            f"but {len(apply_res['failed'])} block(s) failed.\n"
            f"{failed_previews}\n"
            f"Re-issue ONLY the failed SEARCH/REPLACE blocks using anchors "
            f"copied verbatim from the current file."
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