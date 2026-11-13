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
from tools.code_tools import fix_unclosed_brackets

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
        """Get available symbols in content with robust AST parsing and error handling"""
        if not content:
            return []
            
        if lang == "python":
            try:
                tree = ast.parse(content)
                symbols = []
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)):
                        try:
                            start_line = node.lineno - 1
                            end_line = getattr(node, 'end_lineno', node.lineno)
                            
                            if node.decorator_list:
                                start_line = min(d.lineno - 1 for d in node.decorator_list)
                            
                            source_lines = content.splitlines()[start_line:end_line]
                            full_source = '\n'.join(source_lines)
                            
                            symbols.append({
                                "name": node.name,
                                "type": type(node).__name__,
                                "lineno": node.lineno,
                                "end_lineno": end_line,
                                "source": full_source,
                            })
                        except (AttributeError, IndexError):
                            continue
                return symbols
            except SyntaxError:
                return []
        elif lang == "javascript":
            symbols = []
            lines = content.splitlines()
            pattern = r"(?:(function\s+(\w+)\s*\()|(class\s+(\w+)\s*{)|(const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>)"
            for match in re.finditer(pattern, content):
                start_line = content.count('\n', 0, match.start()) + 1
                if match.group(2):
                    brace_level = 0
                    pos = match.end()
                    while pos < len(content):
                        if content[pos] == '{': brace_level += 1
                        elif content[pos] == '}':
                            brace_level -= 1
                            if brace_level == 0: break
                        pos += 1
                    end_line = content.count('\n', 0, pos) + 1
                    source = '\n'.join(lines[start_line-1:end_line])
                    
                    symbols.append({
                        "name": match.group(2) or match.group(6),
                        "type": "FunctionDef",
                        "lineno": start_line,
                        "end_lineno": end_line,
                        "source": source
                    })
                else:
                    brace_level = 0
                    pos = match.end()
                    while pos < len(content):
                        if content[pos] == '{': brace_level += 1
                        elif content[pos] == '}':
                            brace_level -= 1
                            if brace_level == 0: break
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
            try:
                tree = ast.parse(content)
                lines = content.splitlines()
                modified = False
                
                for node in ast.walk(tree):
                    if isinstance(node, ast.FunctionDef) and node.name == self.name:
                        try:
                            new_func = ast.parse(self.new_body)
                            if not isinstance(new_func, ast.Module) or len(new_func.body) != 1:
                                raise ValueError("Function body must contain exactly one function definition")
                            if not isinstance(new_func.body[0], ast.FunctionDef):
                                raise ValueError("Function body must be a function definition")
                        except SyntaxError as e:
                            raise ValueError(f"Invalid function body: {str(e)}")
                        
                        # === FIX 2: sanity check — reject destructive replacements ===
                        _orig_lines = node.end_lineno - node.lineno
                        _new_lines = len(self.new_body.splitlines())
                        if _orig_lines > 10 and _new_lines < _orig_lines * 0.6:
                            raise ValueError(
                                f"DESTRUCTIVE REPLACEMENT: '{self.name}' has {_orig_lines} lines in original, "
                                f"but new_body has only {_new_lines}. This suggests you deleted logic. "
                                f"Include ALL existing lines in new_body — only add/modify what's needed. "
                                f"Or use 'insert_code' operation instead."
                            )
                        
                        _stub_markers = [
                            "# ... existing", "# rest of",
                            "# todo: keep", "# original implementation",
                            "# rest of the original",
                        ]
                        if any(m in self.new_body.lower() for m in _stub_markers):
                            raise ValueError(
                                f"STUB DETECTED in replacement for '{self.name}'. "
                                f"Do not use placeholders like '# ... existing code'. "
                                f"Use 'insert_code' operation instead, or include full function body."
                            )
                        
                        start_line = node.lineno - 1
                        end_line = node.end_lineno
                        
                        if node.decorator_list:
                            start_line = min(d.lineno - 1 for d in node.decorator_list)
                        
                        indent = ' ' * node.col_offset
                        new_body = [indent + line for line in self.new_body.splitlines()]
                        
                        lines = lines[:start_line] + new_body + lines[end_line:]
                        modified = True
                        break
                
                if not modified:
                    raise ValueError(f"Function '{self.name}' not found")
                
                return "\n".join(lines)
            except SyntaxError as e:
                raise ValueError(f"Target file syntax error: {str(e)}")
        return content

class ReplaceClass(ModificationPrimitive):
    """Replace a class definition with new content"""
    
    def __init__(self, name: str, new_content: str):
        self.name = name
        self.new_content = new_content
        
    def apply(self, content: str, lang: str) -> str:
        if lang == "python":
            try:
                tree = ast.parse(content)
                lines = content.splitlines()
                modified = False
                
                for node in ast.walk(tree):
                    if isinstance(node, ast.ClassDef) and node.name == self.name:
                        start_line = node.lineno - 1
                        end_line = node.end_lineno
                        
                        if node.decorator_list:
                            start_line = min(d.lineno - 1 for d in node.decorator_list)
                        
                        indent = ' ' * node.col_offset
                        new_content = [indent + line for line in self.new_content.splitlines()]
                        
                        while end_line < len(lines) and not lines[end_line].strip():
                            end_line += 1
                            
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
                best_start = sum(len(line) + 1 for line in content_lines[:i])
                best_end = best_start + len(window_str)

    if best_ratio >= 0.85 and best_start is not None:
        return best_start, best_end
        
    return None, None


def _looks_like_full_rewrite(diff_text: str) -> bool:
    if re.search(r"<{4,}\s*SEARCH", diff_text):
        return False
    fences = re.findall(r"```(?:\w+)?\n(.*?)```", diff_text, re.DOTALL)
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
            target_path = os.path.join(os.getcwd(), target_file)

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
                
            elif op_type == "fix_syntax":
                from tools.code_tools import fix_unclosed_brackets
                res = fix_unclosed_brackets(target_path)
                return {"ok": res["ok"], "code": original_content, "matched": 1, "failed": [], "mode": "tool_fix_syntax"}

            elif op_type == "check_syntax":
                from tools.code_tools import run_syntax_check
                res = run_syntax_check(target_path)
                return {"ok": res["ok"], "code": original_content, "matched": 1, "failed": [{"reason": res.get("reason", "")}] if not res["ok"] else [], "mode": "tool_check_syntax"}
                
            elif op_type == "run_snippet":
                from tools.code_tools import run_snippet
                res = run_snippet(op_data.get("code", ""), timeout=op_data.get("timeout", 2))
                return {"ok": res["ok"], "code": original_content, "matched": 1, "failed": [{"reason": res.get("stderr", "")}] if not res["ok"] else [], "mode": "tool_run_snippet"}
                
            elif op_type == "check_imports":
                from tools.code_tools import check_imports
                res = check_imports(target_path)
                return {"ok": res["ok"], "code": original_content, "matched": 1, "failed": [{"reason": f"Missing: {m}"} for m in res.get("missing", [])], "mode": "tool_check_imports"}
                
            elif op_type == "add_dependency":
                from tools.code_tools import add_dependency
                res = add_dependency(op_data.get("package", ""))
                return {"ok": res["ok"], "code": original_content, "matched": 1, "failed": [{"reason": res.get("reason", "")}] if not res["ok"] else [], "mode": "tool_add_dependency"}
                
            elif op_type == "insert_code":
                from tools.code_tools import insert_code_in_function
                res = insert_code_in_function(
                    target_path,
                    op_data.get("function", ""),
                    op_data.get("anchor", ""),
                    op_data.get("code", ""),
                )
                return {"ok": res["ok"], "code": original_content, "matched": 1, "failed": [{"reason": res.get("reason", "")}] if not res["ok"] else [], "mode": "tool_insert_code"}
                
        except Exception as e:
            return {
                "ok": False, "code": original_content, "matched": 0,
                "failed": [{"reason": f"AST_OPERATION_FAILED: {str(e)}"}],
                "mode": "rejected"
            }

    is_empty_file = not original_content.strip()

    if target_file.endswith((".html", ".htm")) and len(original_content) > 200:
        allow_full_rewrite = False

    full_rewrite_intent = is_empty_file or allow_full_rewrite or _looks_like_full_rewrite(diff_text)

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

        if target_file.endswith((".js", ".mjs", ".cjs")):
            import tempfile
            import subprocess
            tmp_path = None
            try:
                with tempfile.NamedTemporaryFile(mode="w", suffix=".js", delete=False, encoding="utf-8") as tmp:
                    tmp.write(candidate_code)
                    tmp_path = tmp.name
                check = subprocess.run(["node", "--check", tmp_path], capture_output=True, text=True, timeout=12)
                if check.returncode != 0:
                    err = (check.stderr or check.stdout or "").strip()[:500]
                    return {"ok": False, "code": original_content, "matched": 0, "failed": [{"reason": f"JS_SYNTAX_ERROR: {err}"}], "mode": "rejected"}
            except Exception:
                pass
            finally:
                if tmp_path and os.path.exists(tmp_path):
                    try: os.remove(tmp_path)
                    except OSError: pass

        return {"ok": True, "code": candidate_code, "matched": 0, "failed": [], "mode": "full_rewrite"}

    # Mode B: SEARCH/REPLACE
    pattern = r"<{4,}\s*SEARCH[ \t]*\n(.*?)\n?={4,}[ \t]*\n(.*?)\n?>{4,}\s*REPLACE"
    matches = re.findall(pattern, diff_text, re.DOTALL)

    if not matches:
        return {"ok": False, "code": original_content, "matched": 0, "failed": [{"reason": "NO_SEARCH_BLOCKS"}], "mode": "rejected"}

    updated_content = original_content
    failed_blocks = []
    matched_count = 0

    for idx, (search_block, replace_block) in enumerate(matches, 1):
        start, end = _flexible_find(updated_content, search_block)
        if start is not None:
            updated_content = updated_content[:start] + replace_block + updated_content[end:]
            matched_count += 1
        else:
            failed_blocks.append({"block_index": idx, "search_preview": search_block[:150], "reason": "NOT_FOUND"})

    if matched_count == 0:
        return {"ok": False, "code": original_content, "matched": 0, "failed": failed_blocks, "mode": "rejected"}

    return {"ok": True, "code": updated_content, "matched": matched_count, "failed": failed_blocks, "mode": "search_replace" if not failed_blocks else "partial_search_replace"}


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
        ContextBundle, Tier, TierItem, ContextAssembler, smart_slice_code, dedupe_logs,
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
            role="editor", query=retrieval_query, limit=getattr(config, "LESSONS_RETRIEVAL_LIMIT", 5),
        )
        if not lessons_block:
            lessons_block = get_promoted_lessons_prompt()
        state["active_lesson_node_ids"] = list(lesson_node_ids or [])

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
        "research": state.get("research_analysis") or state.get("research_data", {}).get("analysis", ""),
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
        "INSTRUCTION: You can use EITHER SEARCH/REPLACE blocks OR a JSON AST Modification.\n\n"
        "Option A (Preferred): JSON AST Modification\n"
        "Return EXACTLY one JSON object inside a ```json block:\n"
        "{\"operation\": \"replace_function\", \"name\": \"func_name\", \"new_body\": \"def func_name():\\n    pass\"}\n"
        "Available operations: 'replace_function', 'replace_class', 'insert_after', 'fix_syntax', 'insert_code'.\n\n"
        "Option A+ (Auto-Fix Tools - HIGHLY RECOMMENDED):\n"
        "- To format the whole file: {\"operation\": \"format\"}\n"
        "- To lint the file and find errors: {\"operation\": \"lint\"}\n"
        "- To add a missing import: {\"operation\": \"add_import\", \"module\": \"os\", \"name\": \"path\"}\n\n"
        "Level 2 Tools (execution):\n"
        "- To verify syntax: {\"operation\": \"check_syntax\"}\n"
        "- To run isolated Python snippet: {\"operation\": \"run_snippet\", \"code\": \"print(2+2)\"}\n"
        "- To check all imports resolve: {\"operation\": \"check_imports\"}\n"
        "- To install a dependency: {\"operation\": \"add_dependency\", \"package\": \"requests\"}\n\n"
        "Option B (Fallback): SEARCH/REPLACE\n"
        "<<<<<<< SEARCH\n(verbatim lines from the file)\n=======\n(replacement lines)\n>>>>>>> REPLACE\n"
        "CRITICAL RULE: If the file is truncated, use the JSON AST Modification option targeting the function name."
    )

    coherence_note = ""
    if related_context:
        coherence_note = (
            "=== CRITICAL: CROSS-FILE COHERENCE ===\n"
            "The RELATED FILES section below shows other files in this project. "
            "You MUST use the exact class names, CSS variables, and function names "
            "already defined there.\n\n"
        )

    bundle = ContextBundle()
    bundle.add_text(Tier.T0_STATIC, "skills", system_prompt)
    action_core = (
        f"{coherence_note}"
        f"=== FILE WRITING MODE ===\n{file_mode_instruction}\n\n"
        f"=== SPECIFICATION ===\n{json.dumps(editor_payload, ensure_ascii=False)}"
    )
    bundle.add_text(Tier.T1_ACTION_CORE, "spec", action_core)

    if session_context:
        bundle.add_text(Tier.T1_ACTION_CORE, "session", session_context)

    # === FIX 1: inject full function source when replace_function is expected ===
    _subtask_lower = str(state.get("active_subtask") or "").lower()
    if "replace_function" in _subtask_lower and target_file.endswith(".py") and target_code:
        import ast as _ast_ctx
        try:
            _tree_ctx = _ast_ctx.parse(target_code)
            for _node_ctx in _tree_ctx.body:
                if isinstance(_node_ctx, _ast_ctx.FunctionDef) and _node_ctx.name.lower() in _subtask_lower:
                    _full_src = _ast_ctx.get_source_segment(target_code, _node_ctx)
                    if _full_src:
                        bundle.add_text(
                            Tier.T1_ACTION_CORE,
                            "full_function_under_edit",
                            f"=== FULL SOURCE OF '{_node_ctx.name}' (preserve ALL logic) ===\n```python\n{_full_src}\n```"
                        )
                    break
        except Exception:
            pass

    if target_code:
        if is_new_or_empty:
            bundle.add_text(Tier.T2_TARGET_SLICE, "target", f"=== TARGET FILE ({target_file}) ===\n[empty]")
        else:
            hints = " ".join(filter(None, [str(state.get("active_subtask") or ""), str(state.get("research_analysis") or "")[:400]]))
            is_js = target_file.endswith((".js", ".mjs", ".cjs", ".ts", ".tsx"))
            max_normal = 900 if is_js and len(target_code.splitlines()) < 900 else (500 if is_js else 150)
            max_tight = 300 if is_js and len(target_code.splitlines()) < 900 else (180 if is_js else 50)
            
            sliced = smart_slice_code(target_code, target_file, max_lines=max_normal, hints=hints)
            tightly = smart_slice_code(target_code, target_file, max_lines=max_tight, hints=hints)
            
            bundle.add(TierItem(
                tier=Tier.T2_TARGET_SLICE, key="target",
                content=f"=== TARGET FILE ({target_file}) ===\n{sliced}",
                fallback_content=f"=== TARGET FILE ({target_file}) [HEAVILY TRUNCATED] ===\n{tightly}" if tightly != sliced else "",
                optional=False,
            ))

    if related_context:
        bundle.add_text(Tier.T3_INTERFACE, "related", related_context)
    if lessons_block:
        bundle.add_text(Tier.T4_ENRICHMENT, "lessons", lessons_block)

    if iteration_logs.strip():
        if iteration_number > 0 or getattr(config, "TCA_INCLUDE_FEEDBACK_ON_FIRST_CYCLE", False):
            deduped = dedupe_logs(iteration_logs, max_errors=5)
            if deduped:
                bundle.add_text(Tier.T5_FEEDBACK, "logs", f"=== RECENT ERRORS ===\n{deduped}", optional=True)

    assembled = ContextAssembler(
        budget=getattr(config, "TOKEN_BUDGET_EDITOR", 18000),
        safety_margin=getattr(config, "TCA_SAFETY_MARGIN", 0.9),
    ).assemble(bundle)

    if getattr(config, "TCA_LOG_SUMMARY", True):
        try: print(assembled.log_summary())
        except Exception: pass

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
    finish_reason = getattr(choice, "finish_reason", None)

    result_text = ""
    if message:
        result_text = getattr(message, "content", "") or ""
        if not result_text.strip() and hasattr(message, "reasoning_content"):
            result_text = getattr(message, "reasoning_content", "") or ""

    result_text = result_text.strip()

    if not result_text:
        raise RuntimeError(f"Error: Editor received empty response ({active_model}). finish_reason: {finish_reason}")

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

    if "=======" in result_text and "<<<<<<<" not in result_text:
        result_text = result_text.replace("=======", "")
        result_text = re.sub(r"={4,}", "", result_text)

    apply_res = apply_diff_blocks(
        target_code, result_text,
        allow_full_rewrite=state.get("allow_full_rewrite", False),
        target_file=target_file,
    )

    state["generated_code"] = {
        "target_file": target_file,
        "action": "modify",
        "code": apply_res["code"],
        "apply_result": apply_res,
    }

    if apply_res.get("mode") == "partial_search_replace" and apply_res.get("failed"):
        failed_previews = "; ".join(f"block {f.get('block_index')}: {f.get('reason', '')[:120]}" for f in apply_res["failed"][:3])
        state["editor_feedback"] = f"PARTIAL APPLY: {apply_res.get('matched', 0)} block(s) applied, {len(apply_res['failed'])} failed.\n{failed_previews}\nRe-issue ONLY the failed blocks."

    usage = getattr(response, "usage", None)
    usage_meta = type("Meta", (), {
        "prompt_token_count": getattr(usage, "prompt_tokens", 0) or 0,
        "candidates_token_count": getattr(usage, "completion_tokens", 0) or 0,
        "raw_response": result_text,
        "provider": provider,
        "model_id": active_model,
    })

    return state, usage_meta