"""
DifferiX orchestrator entry point.

State machine: research → edit → review → (research | done).

Every write to a plan step's target file marks that step completed
immediately; the reviewer's verdict decides whether to keep the write or
revert it. Coherence across files runs only when no plan step remains
pending — mid-plan, a missing file is expected, not a failure.

Files created by the pipeline (as opposed to files that existed before
the pipeline first touched them) are tracked in `pipeline_created_files`.
On rejection, a pipeline-created file is deleted rather than restored —
this prevents the accumulation of partial writes across cycles.
"""

import json
import os
import select
import sys
import re
import time
from datetime import datetime
from typing import Union
from dotenv import load_dotenv

from tools.security.behavior_guard import BehaviorGuard
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()

import config

from tools.file_manager import FolderSandbox
from tools.compiler import run_universal_build
from tools.backup_manager import (
    get_repo,
    make_auto_commit,
    safe_rollback,
    show_all_commits,
    create_success_tag,
)
from tools.visual_reviewer import review_page as visual_review_page
from tools.agents_chat import handle_msg_command
from tools.changelog_engine import ChangelogEngine
from tools.project_structure import generate_or_update_structure
from tools.security_filter import find_dangerous_code
from tools.reasoning_logger import log_reasoning
from tools.project_scanner import get_full_project_context as get_project_context
from tools.drift_guard import DriftGuard
from tools.coherence_checker import check_coherence, render_report as render_coherence_report
from tools.project_context_gatherer import (
    gather_related_context,
    render_related_context,
)
from tools.cost_predictor import predict_cost
from tools.plugin_manager import PluginManager

from agents.researcher import run_researcher
from agents.editor import run_editor
from agents.reviewer import run_reviewer

try:
    from memory.manager import MemoryManager
except ImportError:
    MemoryManager = None

from tools.lesson_manager import (
    LESSONS_DIRS,
    move_to_candidates,
    move_to_promoted,
    move_to_deprecated,
    load_promoted_lessons,
    analyze_lesson_outcome,
    record_lesson_trigger,
    record_lesson_helpful,
    record_lesson_violation,
    lesson_memory_stats,
)
from tools.atomic_changeset import ChangeSet
from tools.contract_validator import ContractValidator
from tools.session_adr import SessionADR, build_entry as build_adr_entry
from tools.handshake import build_session_context

from cli.model_cli import handle_llm_command, sync_active_models_specs

from tools.state_manager import (
    AppState,
    apply_researcher_plan,
    build_state_template,
    load_state,
    save_state,
)

load_dotenv()


# ---------------------------------------------------------------------------
# Framework paths
# ---------------------------------------------------------------------------
_FRAMEWORK_ROOT = os.path.abspath(os.path.dirname(__file__))
_DIFFERIX_DIR = os.path.join(_FRAMEWORK_ROOT, ".differix")

REGISTRY_FILE = os.path.join(_DIFFERIX_DIR, "projects_registry.json")
LEGACY_REGISTRY_FILE = os.path.join(os.getcwd(), "projects_registry.json")


# ---------------------------------------------------------------------------
# stdin handling
# ---------------------------------------------------------------------------
def _drain_stdin() -> None:
    try:
        if not sys.stdin.isatty():
            return
        while select.select([sys.stdin], [], [], 0.0)[0]:
            sys.stdin.readline()
    except Exception:
        pass


_PASTE_ARTIFACT_PREFIXES = ("#", "-", ">", "|", "```", "    ", "   ")


def _looks_like_paste_artifact(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    return stripped.startswith(_PASTE_ARTIFACT_PREFIXES)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
def load_registry() -> dict:
    if os.path.exists(REGISTRY_FILE):
        try:
            with open(REGISTRY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and data:
                return data
        except Exception:
            pass

    if (
        os.path.exists(LEGACY_REGISTRY_FILE)
        and os.path.abspath(LEGACY_REGISTRY_FILE) != os.path.abspath(REGISTRY_FILE)
    ):
        try:
            with open(LEGACY_REGISTRY_FILE, "r", encoding="utf-8") as f:
                legacy = json.load(f)
            if isinstance(legacy, dict) and legacy:
                _write_registry(legacy)
                print(
                    f"[Registry] Migrated from legacy {LEGACY_REGISTRY_FILE} "
                    f"to {REGISTRY_FILE}"
                )
                return legacy
        except Exception:
            pass

    cwd = os.getcwd()
    return {
        "1": {
            "name": os.path.basename(cwd) or "Current Workspace",
            "path": cwd,
            "lang": ".py",
        }
    }


def save_registry(registry: dict) -> None:
    _write_registry(registry)


def _write_registry(registry: dict) -> None:
    os.makedirs(os.path.dirname(REGISTRY_FILE), exist_ok=True)
    tmp = REGISTRY_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(registry, f, indent=2, ensure_ascii=False)
        os.replace(tmp, REGISTRY_FILE)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------
def extract_json_payload(raw_text: str) -> Union[dict, list]:
    if not isinstance(raw_text, str):
        return raw_text

    match = re.search(
        r"```(?:json)?\s*([\[\{].*?[\]\}])\s*```", raw_text, re.DOTALL
    )
    candidate = match.group(1) if match else raw_text.strip()

    start_obj = candidate.find("{")
    start_arr = candidate.find("[")

    if start_arr != -1 and (start_obj == -1 or start_arr < start_obj):
        end_arr = candidate.rfind("]")
        if end_arr != -1:
            candidate = candidate[start_arr: end_arr + 1]
    elif start_obj != -1:
        end_obj = candidate.rfind("}")
        if end_obj != -1:
            candidate = candidate[start_obj: end_obj + 1]

    return json.loads(candidate)


def sanitize_generated_code(code_str: str, allow_patches: bool = False) -> str:
    if not isinstance(code_str, str):
        raise ValueError(
            "Code input must be a string, got: " + type(code_str).__name__
        )

    fence_match = re.search(
        r"```(?:\w+)?\n(.*?)```", code_str.strip(), re.DOTALL
    )
    if fence_match:
        code_str = fence_match.group(1)

    cleaned_lines = []
    for line in code_str.splitlines():
        stripped = line.strip()

        if re.match(r"^(<{4,}|={4,}|>{4,})", stripped):
            continue

        if stripped.startswith("```") and not allow_patches:
            continue

        cleaned_lines.append(line)

    result = "\n".join(cleaned_lines).strip()

    if not result:
        raise ValueError(
            "Code payload is empty after sanitization. "
            "The LLM response may have contained only conflict markers or code fences."
        )

    return result


def validate_required_state_fields(state: dict) -> tuple:
    if not isinstance(state, dict):
        return False, ["state (must be dict)"]

    if not state.get("task_prompt") and state.get("goal"):
        state["task_prompt"] = state["goal"]
    elif not state.get("goal") and state.get("task_prompt"):
        state["goal"] = state["task_prompt"]

    required_fields = ["task_prompt", "current_step", "iteration_count"]
    missing = [f for f in required_fields if f not in state or state[f] is None]

    if "task_prompt" in state and not str(state.get("task_prompt", "")).strip():
        if "task_prompt (cannot be empty)" not in missing:
            missing.append("task_prompt (cannot be empty)")

    return len(missing) == 0, missing


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
class OrchestratorConsole:
    DIFFERIX_FRAMEWORK_ROOT = _FRAMEWORK_ROOT

    def __init__(self):
        self.total_cost = 0.0
        self.should_pause = False
        self.should_stop = False
        self.registry = load_registry()

        self.proj = self.registry.get("1")
        if self.proj and os.path.exists(self.proj["path"]):
            self.sandbox = FolderSandbox(self.proj["path"])
            self.logger = ChangelogEngine(project_root=self.proj["path"])
            self.memory = (
                MemoryManager(db_path=os.path.join(self.proj["path"], "memory.db"))
                if MemoryManager
                else None
            )
            self.adr = SessionADR(self.proj["path"])
            self.validator = ContractValidator(self.proj["path"])
            self._guard = DriftGuard(self.proj["path"])
            self.plugin_mgr = PluginManager(self.proj["path"])
        else:
            self.sandbox = None
            self.logger = None
            self.memory = MemoryManager() if MemoryManager else None
            self.adr = None
            self.validator = None
            self._guard = None
            self.plugin_mgr = None

    # ------------------------------------------------------------------ #
    # State I/O
    # ------------------------------------------------------------------ #
    def get_state_path(self, project_dir: str = None) -> str:
        target_dir = project_dir or (self.proj["path"] if self.proj else None)
        if target_dir and os.path.isdir(target_dir):
            return os.path.join(target_dir, "state.json")
        return os.path.join(self.DIFFERIX_FRAMEWORK_ROOT, "state.json")

    def load_state(self, project_dir: str = None) -> dict:
        state_file = self.get_state_path(project_dir)

        if self._guard is not None:
            if not self._guard.check_state("last-save", state_file):
                console.print(
                    "[dim]state.json changed externally — reloading from disk.[/dim]"
                )

        app_state = load_state(state_file)

        if hasattr(app_state, "model_dump"):
            data = app_state.model_dump()
        elif isinstance(app_state, dict):
            data = app_state
        else:
            data = {}

        exec_ctrl = data.get("execution_control", {}) or {}
        task_spec = data.get("task_spec", {}) or {}

        data["current_step"] = exec_ctrl.get("current_step", "init")
        data["status"] = exec_ctrl.get("status", "idle")
        data["active_agent"] = exec_ctrl.get("active_agent", "researcher")
        data["iteration_count"] = exec_ctrl.get("iteration_count", 0)

        data["task_prompt"] = task_spec.get("raw_user_prompt", "")
        data["goal"] = (
            task_spec.get("raw_user_prompt", "")
            or task_spec.get("decomposed_goal", "")
        )
        return data

    def save_state(self, state: Union[dict, AppState], project_dir: str = None):
        state_file = self.get_state_path(project_dir)

        if isinstance(state, AppState):
            state = state.model_dump()

        if isinstance(state, dict):
            if "execution_control" not in state or not isinstance(
                state["execution_control"], dict
            ):
                state["execution_control"] = {}
            if "task_spec" not in state or not isinstance(
                state["task_spec"], dict
            ):
                state["task_spec"] = {}

            if "current_step" in state:
                state["execution_control"]["current_step"] = state["current_step"]
            if "status" in state:
                state["execution_control"]["status"] = state["status"]
            if "active_agent" in state:
                state["execution_control"]["active_agent"] = state["active_agent"]
            if "iteration_count" in state:
                state["execution_control"]["iteration_count"] = state["iteration_count"]

            if "task_prompt" in state:
                state["task_spec"]["raw_user_prompt"] = state["task_prompt"]
            if "goal" in state and not state["task_spec"].get("raw_user_prompt"):
                state["task_spec"]["raw_user_prompt"] = state["goal"]

            app_state = AppState.model_validate(state)
        else:
            app_state = state

        save_state(app_state, state_file)

        if self._guard is not None:
            self._guard.pin_state("last-save", state_file)

    # ------------------------------------------------------------------ #
    # Plan step helpers
    # ------------------------------------------------------------------ #
    def _mark_step_completed(self, state: dict, target_file: str) -> bool:
        if not target_file:
            return False
        plan = state.get("plan", [])
        if not isinstance(plan, list):
            return False

        for step in plan:
            if not isinstance(step, dict):
                continue
            if (
                step.get("target_file") == target_file
                and step.get("status") in ("pending", "in_progress")
            ):
                step["status"] = "completed"
                state["plan"] = plan
                return True
        return False

    def _mark_step_pending(self, state: dict, target_file: str) -> bool:
        if not target_file:
            return False
        plan = state.get("plan", [])
        if not isinstance(plan, list):
            return False

        for step in plan:
            if not isinstance(step, dict):
                continue
            if (
                step.get("target_file") == target_file
                and step.get("status") == "completed"
            ):
                step["status"] = "pending"
                state["plan"] = plan
                return True
        return False

    def _coherence_should_run(self, state: dict) -> bool:
        plan = state.get("plan", [])
        if not plan:
            return False
        pending = [
            s for s in plan
            if isinstance(s, dict) and s.get("status") == "pending"
        ]
        return len(pending) == 0

    def _check_rejection_loop(self, state: dict, reason: str) -> bool:
        """
        Return True if the same rejection reason has appeared at least 3
        times in a row.
        """
        import re as _re

        def _normalize(text: str) -> str:
            t = (text or "").lower()
            t = _re.sub(r"[^\w\s]", " ", t)
            t = _re.sub(r"\b(contains?|has|have|found|blocking|across|project|files?)\b", " ", t)
            t = _re.sub(r"\s+", " ", t).strip()
            return " ".join(t.split()[:8])

        key = _normalize(reason)
        history = state.get("recent_rejections", [])
        if not isinstance(history, list):
            history = []

        history.append(key)
        history = history[-5:]
        state["recent_rejections"] = history

        if len(history) >= 3 and len(set(history[-3:])) == 1:
            return True
        return False

    # ------------------------------------------------------------------ #
    # Pipeline-created file tracking
    # ------------------------------------------------------------------ #
    def _track_pipeline_file(self, state: dict, rel_path: str) -> None:
        if not rel_path:
            return
        created = state.get("pipeline_created_files", [])
        if not isinstance(created, list):
            created = []
        if rel_path not in created:
            created.append(rel_path)
        state["pipeline_created_files"] = created

    def _untrack_pipeline_file(self, state: dict, rel_path: str) -> None:
        created = state.get("pipeline_created_files", [])
        if isinstance(created, list) and rel_path in created:
            created.remove(rel_path)
            state["pipeline_created_files"] = created

    def _is_pipeline_file(self, state: dict, rel_path: str) -> bool:
        created = state.get("pipeline_created_files", [])
        return isinstance(created, list) and rel_path in created

    def _revert_file(self, state: dict, rel_path: str, backup: str) -> None:
        if not rel_path:
            return
        abs_path = os.path.join(self.proj["path"], rel_path)

        if backup:
            self.sandbox.safe_write_code(rel_path, backup)
            self._untrack_pipeline_file(state, rel_path)
            return

        try:
            if os.path.exists(abs_path):
                os.remove(abs_path)
                console.print(f"[dim]Removed pipeline-created file: {rel_path}[/dim]")
        except OSError as exc:
            console.print(
                f"[dim yellow]Could not remove {rel_path}: {exc}[/dim yellow]"
            )
        self._untrack_pipeline_file(state, rel_path)

    def _check_critical_files_intact(self) -> list:
        critical_files = (
            "main.py",
            "config.py",
            "tools/state_manager.py",
            "tools/file_manager.py",
            "tools/atomic_changeset.py",
            "tools/security_filter.py",
            "memory/schemas.py",
            "memory/storage.py",
            "agents/researcher.py",
            "agents/editor.py",
            "agents/reviewer.py",
        )
        missing = []
        for rel in critical_files:
            path = os.path.join(self.proj["path"], rel)
            if not os.path.exists(path):
                missing.append(rel)
        return missing

    # ------------------------------------------------------------------ #
    # Context builders
    # ------------------------------------------------------------------ #
    def _build_session_context(
        self, state: dict, role: str, record_trigger: bool = False
    ) -> str:
        try:
            result = build_session_context(
                project_root=self.proj["path"],
                state=state,
                role=role,
            )
            state["active_lesson_node_ids"] = list(result.lesson_node_ids or [])
            if record_trigger and result.lesson_node_ids:
                try:
                    record_lesson_trigger(result.lesson_node_ids)
                except Exception as trig_exc:
                    console.print(
                        f"[dim yellow]Record trigger failed: {trig_exc}[/dim yellow]"
                    )
            return result.text
        except Exception as exc:
            console.print(f"[dim yellow]Handshake build failed: {exc}[/dim yellow]")
            return ""

    def _build_related_context(self, state: dict, target_file: str) -> str:
        try:
            ctx = gather_related_context(
                project_root=self.proj["path"],
                target_file=target_file,
                plan=state.get("plan", []),
                budget_tokens=getattr(config, "EDITOR_RELATED_BUDGET", 2000),
            )
            rendered = render_related_context(ctx)
            if rendered:
                console.print(
                    f"[dim]Related context: {len(ctx.files)} file(s), "
                    f"{ctx.total_chars} chars[/dim]"
                )
            return rendered
        except Exception as exc:
            console.print(f"[dim yellow]Related context failed: {exc}[/dim yellow]")
            return ""

    def _build_coherence_report(self, state: dict) -> str:
        try:
            pipeline_files = state.get("pipeline_created_files") or []
            if not pipeline_files:
                return ""

            report = check_coherence(self.proj["path"], files=pipeline_files)
            rendered = render_coherence_report(report)
            if report.errors:
                console.print(
                    f"[yellow]Coherence: {len(report.errors)} error(s), "
                    f"{len(report.warnings)} warning(s) "
                    f"(scoped to {len(pipeline_files)} pipeline files)[/yellow]"
                )
            return rendered
        except Exception as exc:
            console.print(f"[dim yellow]Coherence check failed: {exc}[/dim yellow]")
            return ""

    # ------------------------------------------------------------------ #
    # UI helpers
    # ------------------------------------------------------------------ #
    def print_projects_table(self):
        table = Table(
            title="Remembered Projects",
            show_header=True,
            header_style="bold green",
        )
        table.add_column("Key", justify="center", style="bold yellow")
        table.add_column("Project Name", style="white")
        table.add_column("Path", style="cyan")
        table.add_column("Lang", style="magenta")

        for k, v in self.registry.items():
            is_active = (
                self.proj
                and os.path.abspath(v["path"]) == os.path.abspath(self.proj["path"])
            )
            active_marker = (
                " [bold green](active)[/bold green]" if is_active else ""
            )
            table.add_row(
                k, v["name"] + active_marker, v["path"], v.get("lang", ".py")
            )

        console.print(table)
        console.print(f"[dim]Registry: {REGISTRY_FILE}[/dim]")

    def show_status(self):
        state = self.load_state()
        table = Table(
            title="System Status", show_header=True, header_style="bold cyan"
        )
        table.add_column("Property", style="yellow")
        table.add_column("Value", style="white")

        table.add_row("Active Project", self.proj["name"] if self.proj else "None")
        table.add_row("Workspace Path", self.proj["path"] if self.proj else "None")
        table.add_row("Current Step", str(state.get("current_step", "N/A")))
        table.add_row("Iteration Count", str(state.get("iteration_count", 0)))
        table.add_row("Session Cost", f"${self.total_cost:.5f}")

        compiler_log = str(state.get("compiler_output") or "None")
        table.add_row(
            "Last Compiler Output",
            compiler_log[:100] + "..." if len(compiler_log) > 100 else compiler_log,
        )
        console.print(table)

    def show_changelog(self):
        log_dir = (
            os.path.join(self.proj["path"], "iteration_logs")
            if self.proj
            else os.path.join(self.DIFFERIX_FRAMEWORK_ROOT, "iteration_logs")
        )
        if not os.path.exists(log_dir):
            console.print("[yellow]No changelogs recorded yet.[/yellow]")
            return

        logs = sorted(
            [f for f in os.listdir(log_dir) if f.endswith(".json")], reverse=True
        )[:5]
        if not logs:
            console.print("[yellow]No iteration logs found.[/yellow]")
            return

        console.print(
            Panel("[bold cyan]Recent Iteration Logs[/bold cyan]", border_style="cyan")
        )
        for log_file in logs:
            filepath = os.path.join(log_dir, log_file)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    entry = json.load(f)
                step_display = entry.get("current_step")
                if step_display is None:
                    iterations = entry.get("iterations") or []
                    if iterations:
                        step_display = iterations[-1].get("step")
                console.print(
                    f"[bold yellow]{log_file}[/bold yellow] | "
                    f"Step: [green]{step_display or 'N/A'}[/green]"
                )
            except Exception as e:
                console.print(f"[red]Error reading {log_file}: {e}[/red]")

    def update_costs(self, meta, agent_name: str = None):
        provider = getattr(meta, "provider", "openrouter")
        model_id = getattr(meta, "model_id", None)

        if provider == "ollama_fallback":
            price_in, price_out = 0.0, 0.0
        elif agent_name:
            price_in, price_out, _ctx = config.get_current_price(agent_name)
        else:
            price_in = getattr(config, "PRICE_INPUT_1M", 0.14)
            price_out = getattr(config, "PRICE_OUTPUT_1M", 0.28)

        prompt_tokens = getattr(meta, "prompt_token_count", 0) or 0
        completion_tokens = getattr(meta, "candidates_token_count", 0) or 0

        cost = (prompt_tokens * (price_in / 1_000_000)) + (
            completion_tokens * (price_out / 1_000_000)
        )
        self.total_cost += cost

        table = Table(show_header=True, header_style="bold blue", box=None)
        table.add_column("Model", justify="left", style="cyan")
        table.add_column("Input Tokens", justify="right")
        table.add_column("Output Tokens", justify="right")
        table.add_column("Step Cost", justify="right", style="green")
        table.add_column("Session Total", justify="right", style="bold green")

        model_label = model_id or "unknown"
        if provider == "ollama_fallback":
            model_label += " (local fallback)"

        table.add_row(
            model_label,
            f"{prompt_tokens:,}",
            f"{completion_tokens:,}",
            f"${cost:.5f}",
            f"${self.total_cost:.5f}",
        )
        console.print(table)

    def print_help(self):
        help_text = """
[bold cyan]Available Commands:[/bold cyan]
    [yellow]task / t <prompt>[/yellow]    - Bootstrap project task & plan from CLI prompt
    [yellow]run / start [prompt][/yellow] - Execute multi-agent iteration pipeline
    [yellow]cd <path|key>[/yellow]        - Navigate to path or switch project by key index
    [yellow]projects / ls[/yellow]        - Display remembered project registry
    [yellow]llm <cmd>[/yellow]            - Model configuration and provider selection
    [yellow]msg[/yellow]                  - Chat directly with agents for active workspace
    [yellow]handshake [role][/yellow]     - Preview the session context for a role (debugging)
    [yellow]coherence[/yellow]            - Run cross-file coherence check on active project
    [yellow]status[/yellow]               - Display current execution metrics and active step
    [yellow]pause[/yellow]                - Pause execution after current step completes
    [yellow]stop[/yellow]                 - Halt current agent loop
    [yellow]log / changelog[/yellow]      - Render session changelog summaries
    [yellow]history[/yellow]              - View Git commit log for current project
    [yellow]rollback <sha>[/yellow]       - Revert workspace to specific commit SHA
    [yellow]lesson <cmd>[/yellow]         - Lesson management (list | candidate | promote | deprecate)
    [yellow]help[/yellow]                 - Show command directory
    [yellow]reflect[/yellow]              - Run reflection manually on recent failures
    [yellow]upd-tiers[/yellow]            - Update LLM tiers in llm_selector 
    [yellow]exit / quit[/yellow]          - Exit orchestrator
    [yellow]llm routing on / off[/yellow] - Enable or disable automatic model assignment
"""
        console.print(Panel(help_text.strip(), title="System Help", border_style="cyan"))

    # ------------------------------------------------------------------ #
    # Directory navigation
    # ------------------------------------------------------------------ #
    def change_directory(self, target: str):
        target = target.strip()
        if not target:
            self.print_projects_table()
            return

        if target.isdigit():
            if target in self.registry:
                proj_data = self.registry[target]
                if not os.path.isdir(proj_data["path"]):
                    console.print(
                        f"[bold red]Error:[/bold red] Directory "
                        f"'{proj_data['path']}' does not exist."
                    )
                    return
                self.proj = proj_data
                self.sandbox = FolderSandbox(self.proj["path"])
                self.logger = ChangelogEngine(project_root=self.proj["path"])
                if MemoryManager:
                    self.memory = MemoryManager(
                        db_path=os.path.join(self.proj["path"], "memory.db")
                    )
                self.adr = SessionADR(self.proj["path"])
                self.validator = ContractValidator(self.proj["path"])
                self._guard = DriftGuard(self.proj["path"])
                self.should_pause = False
                self.should_stop = False
                console.print(
                    f"[bold green]Switched to registered project [{target}]:[/bold green] "
                    f"{self.proj['name']} ([cyan]{self.proj['path']}[/cyan])"
                )
                return
            else:
                console.print(
                    f"[bold red]Error:[/bold red] Project key [{target}] not found."
                )
                return

        expanded = os.path.expanduser(target)
        if os.path.isabs(expanded):
            abs_path = os.path.realpath(expanded)
        else:
            base_dir = (
                self.proj["path"]
                if (self.proj and os.path.isdir(self.proj["path"]))
                else os.getcwd()
            )
            abs_path = os.path.realpath(os.path.join(base_dir, expanded))

        if not os.path.isdir(abs_path):
            console.print(
                f"[bold red]Error:[/bold red] Directory '{abs_path}' does not exist."
            )
            return

        for k, v in self.registry.items():
            if os.path.abspath(v["path"]) == abs_path:
                self.proj = v
                self.sandbox = FolderSandbox(self.proj["path"])
                self.logger = ChangelogEngine(project_root=self.proj["path"])
                if MemoryManager:
                    self.memory = MemoryManager(
                        db_path=os.path.join(self.proj["path"], "memory.db")
                    )
                self.adr = SessionADR(self.proj["path"])
                self.validator = ContractValidator(self.proj["path"])
                self._guard = DriftGuard(self.proj["path"])
                self.should_pause = False
                self.should_stop = False
                console.print(
                    f"[bold green]Switched to registered project [{k}]:[/bold green] "
                    f"{v['name']}"
                )
                return

        lang = ".py"
        ignored_dirs = {
            ".git",
            "node_modules",
            "__pycache__",
            ".venv",
            "venv",
            "bin",
            "obj",
            "build",
        }

        for root, dirs, files in os.walk(abs_path):
            dirs[:] = [d for d in dirs if d not in ignored_dirs]
            for file in files:
                if file.endswith(".cs"):
                    lang = ".cs"
                    break
                elif file.endswith(".luau") or file.endswith(".lua"):
                    lang = ".luau"
                    break
            if lang != ".py":
                break

        new_key = str(len(self.registry) + 1)
        folder_name = os.path.basename(abs_path) or "Project"
        new_proj = {"name": folder_name, "path": abs_path, "lang": lang}

        self.registry[new_key] = new_proj
        save_registry(self.registry)

        self.proj = new_proj
        self.sandbox = FolderSandbox(self.proj["path"])
        self.logger = ChangelogEngine(project_root=self.proj["path"])
        if MemoryManager:
            self.memory = MemoryManager(
                db_path=os.path.join(self.proj["path"], "memory.db")
            )
        self.adr = SessionADR(self.proj["path"])
        self.validator = ContractValidator(self.proj["path"])
        self._guard = DriftGuard(self.proj["path"])
        self.should_pause = False
        self.should_stop = False
        console.print(
            f"[bold green]Registered and switched to new project [{new_key}]:[/bold green] "
            f"{folder_name} ([cyan]{abs_path}[/cyan])"
        )

    # ------------------------------------------------------------------ #
    # Task command
    # ------------------------------------------------------------------ #
    def handle_task_command(self, prompt: str):
        prompt = prompt.strip()

        if prompt.startswith("-f "):
            filepath = prompt[3:].strip()
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    prompt = f.read().strip()
                console.print(f"[green]Prompt loaded from {filepath} ({len(prompt)} chars)[/green]")
            except Exception as e:
                console.print(f"[bold red]Failed to read {filepath}: {e}[/bold red]")
                return

        if not prompt:
            console.print("[bold yellow]Enter a multi-line prompt. Type 'EOF' on a new line to finish.[/bold yellow]")
            lines = []
            while True:
                try:
                    line = console.input()
                except (KeyboardInterrupt, EOFError):
                    console.print("\n[yellow]Cancelled.[/yellow]")
                    return
                if line.strip() == "EOF":
                    break
                lines.append(line)
            prompt = "\n".join(lines).strip()

            if not prompt:
                console.print("[bold red]Prompt cannot be empty. Cancelled.[/bold red]")
                return

        if not self.proj or not os.path.isdir(self.proj["path"]):
            console.print(
                "[bold red]Error:[/bold red] No valid workspace selected. "
                "Use 'cd <path>' first."
            )
            return

        app_state = build_state_template(user_prompt=prompt)
        self.save_state(app_state)
        state = self.load_state()

        live_project_tree = get_project_context(self.proj["path"])
        state["project_files"] = live_project_tree

        console.print(
            Panel(
                f"[bold cyan]Bootstrapping Task:[/bold cyan]\n{prompt}",
                border_style="cyan",
            )
        )

        session_ctx = self._build_session_context(
            state, role="researcher", record_trigger=True
        )

        with console.status(
            "[cyan]Researcher agent structuring plan and requirements...",
            spinner="dots",
        ):
            state, meta = run_researcher(
                state,
                self.proj["name"],
                project_dir=self.proj["path"],
                project_files=live_project_tree,
                session_context=session_ctx,
            )

        self.update_costs(meta, agent_name="researcher")

        research_data = state.get("research_data", {})
        res_payload = (
            research_data
            if isinstance(research_data, dict)
            else {"raw_response": research_data}
        )

        app_state = AppState.model_validate(state)
        app_state = apply_researcher_plan(app_state, res_payload)

        # Predict costs for the plan
        try:
            cost_prediction = predict_cost(list(app_state.plan), task_prompt=prompt)
            if not hasattr(app_state, "metrics") or app_state.metrics is None:
                app_state.metrics = {}
            if isinstance(app_state.metrics, dict):
                app_state.metrics["predicted_cost"] = cost_prediction
            else:
                setattr(app_state.metrics, "predicted_cost", cost_prediction)

            # Rich Table для вывода прогноза
            cost_table = Table(title="Predicted Cost", show_header=True, header_style="bold green")
            cost_table.add_column("Step", style="cyan")
            cost_table.add_column("Model", style="yellow")
            cost_table.add_column("Complexity", style="magenta")
            cost_table.add_column("~USD", justify="right", style="green")

            for step in cost_prediction.get("by_step", []):
                cost_table.add_row(
                    str(step.get("title", "Unknown"))[:40],
                    str(step.get("model", "unknown")),
                    str(step.get("complexity", "unknown")),
                    f"${step.get('cost_usd', 0.0):.4f}"
                )

            cost_table.add_row(
                "TOTAL (with 30% overhead)", "", "",
                f"${cost_prediction.get('total_usd', 0.0):.4f}",
                style="bold green"
            )
            console.print(cost_table)
        except Exception as cost_exc:
            console.print(f"[dim yellow]Cost prediction failed: {cost_exc}[/dim yellow]")

        self.save_state(app_state)
        state = self.load_state()

        log_reasoning(
            "researcher",
            "research",
            {
                "analysis": state.get("task_spec", {}).get("decomposed_goal", ""),
                "plan": state.get("plan", []),
            },
            project_root=self.proj["path"],
        )

        plan = state.get("plan", [])
        if not plan:
            console.print(
                Panel(
                    f"[bold red]Researcher produced no plan.[/bold red] "
                    f"{state.get('research_error', '')}\n"
                    "Check model/token limits ('llm' command) and retry.",
                    border_style="red",
                )
            )

        target_file = state.get("target_file") or (
            research_data.get("target_file")
            if isinstance(research_data, dict)
            else None
        )
        active_subtask = state.get("active_subtask") or (
            plan[0].get("title") if plan and isinstance(plan[0], dict) else None
        )

        console.print(
            Panel(
                f"[bold green]Task Bootstrapped Successfully![/bold green]\n"
                f"Target File: [cyan]{target_file or 'Not specified / Needs investigation'}[/cyan]\n"
                f"Active Subtask: [yellow]{active_subtask or 'None'}[/yellow]\n"
                f"Plan Steps: [white]{len(plan)} step(s) identified[/white]\n\n"
                f"Type [bold cyan]run[/bold cyan] to start autonomous execution.",
                border_style="green",
            )
        )

    # ------------------------------------------------------------------ #
    # Lesson command
    # ------------------------------------------------------------------ #
    def handle_lesson_command(self, args: str):
        parts = args.strip().split()
        if not parts:
            console.print(
                "[bold yellow]Usage:[/bold yellow] lesson "
                "[list | candidate | promote | deprecate] <filename>"
            )
            return

        action = parts[0].lower()

        if action == "list":
            for cat, path in LESSONS_DIRS.items():
                files = (
                    [f for f in os.listdir(path) if f.endswith(".json")]
                    if os.path.exists(path)
                    else []
                )
                console.print(
                    f"[bold cyan]{cat.upper()}:[/bold cyan] {len(files)} files"
                )
                for f in files:
                    console.print(f"  - {f}")
            return

        if action in ("candidate", "promote", "deprecate"):
            if len(parts) < 2:
                console.print(
                    f"[bold red]Error:[/bold red] Missing target filename "
                    f"for 'lesson {action}'."
                )
                return
            filename = parts[1]
            try:
                if action == "candidate":
                    success = move_to_candidates(
                        filename, source_category="observations"
                    )
                elif action == "promote":
                    success = move_to_promoted(
                        filename, source_category="candidates"
                    )
                else:
                    success = move_to_deprecated(
                        filename, source_category="promoted"
                    )

                if success:
                    console.print(
                        f"[bold green]Success:[/bold green] Lesson '{filename}' updated."
                    )
                else:
                    console.print(
                        f"[bold red]Error:[/bold red] File '{filename}' not found."
                    )
            except Exception as e:
                console.print(f"[bold red]Failed to update lesson:[/bold red] {e}")
            return

        console.print(f"[bold red]Unknown lesson action:[/bold red] '{action}'")

    # ------------------------------------------------------------------ #
    # Handshake command
    # ------------------------------------------------------------------ #
    def handle_handshake_command(self, args: str):
        role = args.strip().lower() or "researcher"
        if not self.proj or not os.path.isdir(self.proj["path"]):
            console.print(
                "[bold red]Error:[/bold red] No valid workspace selected."
            )
            return

        state = self.load_state()
        try:
            result = build_session_context(
                project_root=self.proj["path"],
                state=state,
                role=role,
            )
        except Exception as e:
            console.print(f"[bold red]Handshake failed:[/bold red] {e}")
            return

        console.print(
            Panel(
                result.text or "(empty)",
                title=f"Handshake preview — role={role}",
                border_style="cyan",
            )
        )
        console.print(
            f"[dim]Sections: {', '.join(result.sections_present) or 'none'} | "
            f"~{result.estimated_tokens} tokens | "
            f"lessons: {len(result.lesson_node_ids)} nodes[/dim]"
        )

    # ------------------------------------------------------------------ #
    # Coherence command
    # ------------------------------------------------------------------ #
    def handle_coherence_command(self, args: str):
        if not self.proj or not os.path.isdir(self.proj["path"]):
            console.print(
                "[bold red]Error:[/bold red] No valid workspace selected."
            )
            return

        try:
            report = check_coherence(self.proj["path"])
        except Exception as exc:
            console.print(f"[bold red]Coherence check failed:[/bold red] {exc}")
            return

        border = "green" if report.ok else "yellow"
        console.print(
            Panel(
                render_coherence_report(report),
                title="Coherence Report",
                border_style=border,
            )
        )
        console.print(
            f"[dim]Files checked: {len(report.files_checked)} | "
            f"HTML classes: {len(report.html_classes)} | "
            f"CSS selectors: {len(report.css_selectors)} | "
            f"errors: {len(report.errors)} | warnings: {len(report.warnings)}[/dim]"
        )

    # ------------------------------------------------------------------ #
    # Plugin command
    # ------------------------------------------------------------------ #
    def handle_plugin_command(self, args):
        from rich.markup import escape

        usage = (
            "[bold yellow]Usage:[/bold yellow] "
            "plugin list | plugin install <path> | plugin remove <id>"
        )

        if self.plugin_mgr is None and self.proj and os.path.isdir(self.proj["path"]):
            try:
                self.plugin_mgr = PluginManager(self.proj["path"])
            except Exception:
                self.plugin_mgr = None

        if self.plugin_mgr is None:
            console.print(
                Panel(
                    "[bold red]No active project — plugin manager is unavailable.[/bold red]\n"
                    "Use 'cd <path>' to select a workspace first.",
                    title="Plugins",
                    border_style="red",
                )
            )
            return

        if isinstance(args, (list, tuple)):
            tokens = [str(a) for a in args]
        else:
            tokens = str(args or "").strip().split(maxsplit=1)

        if not tokens:
            console.print(usage)
            return

        action = tokens[0].lower()
        rest = ""
        if len(tokens) > 1:
            rest = " ".join(tokens[1:]).strip() if isinstance(args, (list, tuple)) else tokens[1].strip()

        if action == "list":
            try:
                plugins = self.plugin_mgr.list_plugins() or []
            except Exception as exc:
                console.print(
                    Panel(
                        f"[bold red]Failed to list plugins:[/bold red] {escape(str(exc))}",
                        title="Plugins",
                        border_style="red",
                    )
                )
                return

            if not plugins:
                console.print("[dim]No plugins installed[/dim]")
                return

            table = Table(
                title="Installed Plugins",
                show_header=True,
                header_style="bold cyan",
            )
            table.add_column("ID", style="bold yellow")
            table.add_column("Name", style="white")
            table.add_column("Version", style="magenta")
            table.add_column("Author", style="green")

            def _field(item, key):
                if isinstance(item, dict):
                    value = item.get(key)
                else:
                    value = getattr(item, key, None)
                if value is None or value == "":
                    return "-"
                return escape(str(value))

            for item in plugins:
                if isinstance(item, str):
                    table.add_row(escape(item), "-", "-", "-")
                    continue
                table.add_row(
                    _field(item, "id"),
                    _field(item, "name"),
                    _field(item, "version"),
                    _field(item, "author"),
                )
            console.print(table)
            return

        if action == "install":
            if not rest:
                console.print("[bold yellow]Usage:[/bold yellow] plugin install <path>")
                return
            plugin_path = os.path.expanduser(rest.strip().strip('"').strip("'"))
            try:
                result = self.plugin_mgr.install(plugin_path)
            except Exception as exc:
                console.print(
                    Panel(
                        f"[bold red]Plugin install failed:[/bold red] {escape(str(exc))}",
                        title="Plugins",
                        border_style="red",
                    )
                )
                return

            ok = False
            message = ""
            if isinstance(result, tuple):
                ok = bool(result[0]) if result else False
                message = str(result[1]) if len(result) > 1 and result[1] is not None else ""
            elif isinstance(result, dict):
                ok = bool(result.get("ok", result.get("success", False)))
                message = str(
                    result.get("message") or result.get("error") or result.get("id") or ""
                )
            else:
                ok = bool(result)
                message = result if isinstance(result, str) else ""

            if ok:
                console.print(
                    Panel(
                        f"[bold green]Plugin installed from {escape(plugin_path)}[/bold green]"
                        + (f"\n{escape(message)}" if message else ""),
                        title="Plugins",
                        border_style="green",
                    )
                )
            else:
                console.print(
                    Panel(
                        f"[bold red]Plugin install failed for {escape(plugin_path)}[/bold red]"
                        + (f"\n{escape(message)}" if message else ""),
                        title="Plugins",
                        border_style="red",
                    )
                )
            return

        if action == "remove":
            if not rest:
                console.print("[bold yellow]Usage:[/bold yellow] plugin remove <id>")
                return
            plugin_id = rest.strip()
            try:
                removed = self.plugin_mgr.remove(plugin_id)
            except Exception as exc:
                console.print(
                    Panel(
                        f"[bold red]Plugin removal failed:[/bold red] {escape(str(exc))}",
                        title="Plugins",
                        border_style="red",
                    )
                )
                return

            if removed:
                console.print(
                    Panel(
                        f"[bold green]Plugin '{escape(plugin_id)}' removed.[/bold green]",
                        title="Plugins",
                        border_style="green",
                    )
                )
            else:
                console.print(
                    Panel(
                        f"[bold red]Plugin '{escape(plugin_id)}' not found or could not be removed.[/bold red]",
                        title="Plugins",
                        border_style="red",
                    )
                )
            return

        console.print(f"[bold red]Unknown plugin action:[/bold red] '{escape(action)}'")
        console.print(usage)

    # ------------------------------------------------------------------ #
    # Task bootstrap
    # ------------------------------------------------------------------ #
    def ensure_active_task(self, prompt_arg: str = "") -> bool:
        prompt_arg = prompt_arg.strip()

        if prompt_arg:
            app_state = build_state_template(user_prompt=prompt_arg)
            self.save_state(app_state)
            console.print(f"[bold green]Принята новая задача:[/bold green] {prompt_arg}\n")
            return True

        state = self.load_state() or {}
        raw_prompt = (
            state.get("task_prompt")
            or state.get("user_prompt")
            or state.get("task_spec", {}).get("raw_user_prompt")
            or ""
        ).strip()

        status = str(state.get("status", "")).lower()
        current_step = str(state.get("current_step", "")).lower()
        is_finished = status in ("completed", "failed", "done") or current_step in (
            "completed",
            "failed",
            "done",
        )

        if not raw_prompt or is_finished:
            if is_finished:
                console.print(
                    "[bold yellow]Предыдущая задача уже завершена.[/bold yellow]"
                )

            console.print("[bold yellow]Enter a multi-line prompt. Type 'EOF' on a new line to finish.[/bold yellow]")
            lines = []
            while True:
                try:
                    line = console.input()
                except (KeyboardInterrupt, EOFError):
                    console.print("\n[yellow]Ввод отменен.[/yellow]")
                    _drain_stdin()
                    return False
                if line.strip() == "EOF":
                    break
                lines.append(line)

            user_input = "\n".join(lines).strip()

            if not user_input:
                console.print(
                    "[bold red]Промпт не может быть пустым. Запуск отменен.[/bold red]"
                )
                return False

            app_state = build_state_template(user_prompt=user_input)
            self.save_state(app_state)
            console.print(
                "[green]Состояние инициализировано с новой задачей.[/green]\n"
            )
            return True

        if current_step in (None, "", "idle", "init", "none"):
            state["current_step"] = "research"
            state["status"] = "in_progress"
            self.save_state(state)

        return True

    # ------------------------------------------------------------------ #
    # Pipeline execution
    # ------------------------------------------------------------------ #
    def run_agent_cycle(self, prompt_arg: str = ""):
        if not self.proj or not os.path.isdir(self.proj["path"]):
            console.print(
                "[bold red]Error:[/bold red] No valid workspace selected. "
                "Use 'cd <path>' first."
            )
            return

        if not self.sandbox or not self.logger:
            self.sandbox = FolderSandbox(self.proj["path"])
            self.logger = ChangelogEngine(project_root=self.proj["path"])

        if self._guard is None:
            self._guard = DriftGuard(self.proj["path"])

        if not self.ensure_active_task(prompt_arg):
            return

        try:
            sync_active_models_specs()
        except Exception as e:
            console.print(
                f"[dim yellow]Notice: Model specs sync skipped ({e})[/dim yellow]"
            )

        console.print(
            Panel(
                f"[bold green]Starting execution pipeline...[/bold green]\n"
                f"Target: [white]{self.proj['name']}[/white] "
                f"([cyan]{self.proj['path']}[/cyan])",
                border_style="green",
            )
        )

        self.should_pause = False
        self.should_stop = False

        hitl_interval = getattr(config, "HITL_EVERY_N_CYCLES", 3)

        with console.status(
            "[bold blue]Updating project structure tree...", spinner="dots"
        ):
            struct_res = generate_or_update_structure(self.proj["path"])
        console.print(
            f"[green]Project structure synchronized.[/green] "
            f"Tracked files: [yellow]{struct_res['file_count']}[/yellow]"
        )

        res_file = None
        current_diff = None
        old_code_backup = ""

        while True:
            if self.should_stop:
                console.print("[bold red]Execution stopped.[/bold red]")
                self.should_stop = False
                break

            if self.should_pause:
                console.print("[bold yellow]Execution paused.[/bold yellow]")
                self.should_pause = False
                break

            state = self.load_state()
            iteration = state.get("iteration_count", 0)

            live_project_tree = get_project_context(self.proj["path"])
            state["project_files"] = live_project_tree

            # ---- HITL checkpoint ----
            if iteration > 0 and iteration % hitl_interval == 0:
                console.print(
                    Panel(
                        f"[bold yellow]HITL Checkpoint Reached "
                        f"({iteration} cycles completed)[/bold yellow]\n"
                        "Options: [C]ontinue | [F]eedback | [S]top",
                        border_style="yellow",
                    )
                )
                try:
                    cmd = console.input(
                        "[yellow]Select action ([C]/[F]/[S]): [/yellow]"
                    ).strip().lower()
                except (KeyboardInterrupt, EOFError):
                    console.print(
                        "\n[yellow]HITL input cancelled. Pausing execution...[/yellow]"
                    )
                    _drain_stdin()
                    break
                finally:
                    _drain_stdin()

                if cmd == "f":
                    try:
                        feedback_text = console.input(
                            "[bold cyan]Enter feedback for agents: [/bold cyan]"
                        ).strip()
                        state["human_feedback"] = feedback_text
                        state["current_step"] = "research"
                        self.save_state(state)
                        console.print(
                            f"[bold green]Feedback logged:[/bold green] {feedback_text}"
                        )
                    except (KeyboardInterrupt, EOFError):
                        _drain_stdin()
                        break
                    finally:
                        _drain_stdin()
                elif cmd != "c":
                    break

            # ---- Resource limits ----
            if (
                iteration >= config.MAX_ITERATIONS
                or self.total_cost >= config.MAX_BUDGET_USD
            ):
                console.print(
                    Panel(
                        "[bold red]Resource Limit Reached![/bold red]",
                        border_style="red",
                    )
                )
                try:
                    cmd = console.input(
                        "[yellow]Select Action ([C]ontinue / [S]top): [/yellow]"
                    ).strip().lower()
                except (KeyboardInterrupt, EOFError):
                    _drain_stdin()
                    break
                finally:
                    _drain_stdin()
                if cmd == "c":
                    state["iteration_count"] = 0
                    self.save_state(state)
                    continue
                else:
                    break
            # Periodic self-reflection: every N iterations, insert a reflect pass
            # before the normal step dispatch.
            step = state.get("current_step")
            
            # Prevent re-triggering reflection on the same iteration
            last_reflect = state.get("metrics", {}).get("last_reflection", {}).get("iteration", -1)
            if (
                step not in ("reflect", "done", "completed")
                and iteration > 0
                and iteration % getattr(config, "REFLECTION_INTERVAL", 5) == 0
                and iteration != last_reflect
            ):
                state["_pre_reflect_step"] = step
                state["current_step"] = "reflect"
                self.save_state(state)
                continue

            # ============================================================
            # STEP 1: RESEARCH & PLANNING
            # ============================================================
            if step == "research":
                console.rule("[bold cyan]Step 1: Research and Planning[/bold cyan]")

                state["hardware_constraints"] = state.get("system_constraints", [])

                session_ctx = self._build_session_context(
                    state, role="researcher", record_trigger=True
                )

                with console.status(
                    "[cyan]Researcher agent decomposing prompt and building plan...",
                    spinner="dots",
                ):
                    state_updated, meta = run_researcher(
                        state,
                        self.proj["name"],
                        project_dir=self.proj["path"],
                        project_files=live_project_tree,
                        session_context=session_ctx,
                    )

                self.update_costs(meta, agent_name="researcher")

                research_data = state_updated.get("research_data", {})
                res_payload = (
                    research_data
                    if isinstance(research_data, dict)
                    else {"raw_response": research_data}
                )

                app_state = AppState.model_validate(state_updated)
                app_state = apply_researcher_plan(app_state, res_payload)

                final_plan = list(app_state.plan)
                pending_count = sum(1 for s in final_plan if s.status == "pending")

                state_to_save = app_state.model_dump()
                state_to_save["research_error"] = state_updated.get("research_error")
                state_to_save["research_analysis"] = state_updated.get(
                    "research_analysis"
                )
                state_to_save["active_lesson_node_ids"] = state_updated.get(
                    "active_lesson_node_ids"
                ) or []

                log_reasoning(
                    "researcher",
                    "research",
                    {
                        "analysis": app_state.task_spec.decomposed_goal
                        or state_updated.get("research_analysis", ""),
                        "plan": [s.model_dump() for s in final_plan],
                    },
                    project_root=self.proj["path"],
                )

                if pending_count == 0:
                    reason = (
                        state_updated.get("research_error") or "no pending steps"
                    )
                    console.print(
                        Panel(
                            f"[bold red]Researcher returned no usable plan:[/bold red] "
                            f"{reason}\n"
                            "Retrying research (counts toward iteration limit).",
                            border_style="red",
                        )
                    )
                    state_to_save["current_step"] = "research"
                    state_to_save["iteration_count"] = (
                        state.get("iteration_count", 0) + 1
                    )
                    self.save_state(state_to_save)
                    continue

                state_to_save["current_step"] = "edit"
                self.save_state(state_to_save)

                console.print(
                    f"[bold green]Plan generated with {pending_count} pending "
                    f"subtask(s) ({len(final_plan)} total).[/bold green]"
                )

            # ============================================================
            # STEP 2: EDIT (code generation)
            # ============================================================
            elif step == "edit":
                console.rule("[bold yellow]Step 2: Code Generation[/bold yellow]")

                pending_steps = [
                    s
                    for s in state.get("plan", [])
                    if isinstance(s, dict) and s.get("status") == "pending"
                ]
                if not pending_steps:
                    # SAFETY CHECK: Did the agent actually finish a web task?
                    is_web_task = (
                        os.path.exists(os.path.join(self.proj["path"], "index.html")) or 
                        "index.html" in str(state.get("pipeline_created_files", []))
                    )
                    
                    if is_web_task:
                        missing_files = []
                        if not os.path.exists(os.path.join(self.proj["path"], "styles.css")):
                            missing_files.append("styles.css")
                        if not os.path.exists(os.path.join(self.proj["path"], "script.js")):
                            missing_files.append("script.js")
                        if not os.path.exists(os.path.join(self.proj["path"], "llms.txt")):
                            missing_files.append("llms.txt")
                            
                        if missing_files:
                            console.print(
                                f"[bold yellow]WARNING: Plan complete, but web files are missing: "
                                f"{', '.join(missing_files)}. Forcing Research phase to generate them.[/bold yellow]"
                            )
                            state["editor_feedback"] = (
                                f"CRITICAL: You marked the plan as complete, but the following mandatory "
                                f"web files are missing: {', '.join(missing_files)}. "
                                f"You must create a new plan to generate these files."
                            )
                            state["current_step"] = "research"
                            self.save_state(state)
                            continue

                    console.print(
                        "[green]No pending steps remaining. Task is complete.[/green]"
                    )
                    state["current_step"] = "done"
                    state["status"] = "completed"
                    self.save_state(state)
                    continue

                current_subtask = pending_steps[0]
                state["active_subtask"] = current_subtask.get("title")
                state["target_file"] = current_subtask.get(
                    "target_file", state.get("target_file")
                )

                session_ctx = self._build_session_context(
                    state, role="editor", record_trigger=True
                )

                resolved_target = state.get("target_file") or ""
                related_ctx = (
                    self._build_related_context(state, resolved_target)
                    if resolved_target
                    else ""
                )

                try:
                    with console.status(
                        "[yellow]Editor agent generating code changes...",
                        spinner="dots",
                    ):
                        state, meta = run_editor(
                            state,
                            project_files=live_project_tree,
                            project_dir=self.proj["path"],
                            session_context=session_ctx,
                            related_context=related_ctx,
                        )
                    self.update_costs(meta, agent_name="editor")
                # === BEHAVIOR GUARD CHECK ===
                    # Pass project_root to state so guard can check paths
                    state["project_root"] = self.proj["path"]
                    halt_reason = self.behavior_guard.observe_attempt(state, meta)
                    
                    if halt_reason:
                        console.print(
                            Panel(
                                f"[bold red]CIRCUIT BREAKER (Behavior Guard)[/bold red]\n\n"
                                f"{halt_reason}\n\n"
                                f"The pipeline has been halted to prevent token waste "
                                f"and potential sandbox escape.",
                                border_style="bold red",
                            )
                        )
                        state["status"] = "failed"
                        state["current_step"] = "done"
                        self.save_state(state)
                        break # Breaks the while loop
                except Exception as e:
                    err_msg = str(e)
                    console.print(f"[bold red]Editor Error:[/bold red] {err_msg}")
                    
                    # Если ответ обрезался по лимиту токенов — даем явную инструкцию
                    if "truncated due to max_tokens limit" in err_msg:
                        state["editor_feedback"] = (
                            "CRITICAL ERROR: Your previous response was too long and got cut off. "
                            "You MUST output the code in smaller, manageable chunks (e.g., max 150 lines at a time). "
                            "If you are doing a full rewrite, stop and wait for the next cycle to append more."
                        )
                        state["edit_retries"] = state.get("edit_retries", 0) + 1
                        if state["edit_retries"] >= 3:
                            console.print("[bold red]Editor failed 3 times due to truncation. Reverting to Research.[/bold red]")
                            state["current_step"] = "research"
                            state["edit_retries"] = 0
                            state["research_error"] = "Generated code was too large. You must split the plan into smaller files or parts."
                        else:
                            state["current_step"] = "edit"
                    else:
                        state["iteration_count"] = state.get("iteration_count", 0) + 1
                    
                    self.save_state(state)
                    continue

                # ---- Patch application failure ----
                raw_generated = state.get("generated_code", {})
                apply_res = (
                    raw_generated.get("apply_result", {})
                    if isinstance(raw_generated, dict)
                    else {}
                )

                if apply_res and not apply_res.get("ok", True):
                    failed_details = "\n".join(
                        [
                            f"- Block {f.get('block_index', '?')}: {f.get('reason')}\n"
                            f"  SEARCH preview:\n```\n{f.get('search_preview')}\n```"
                            for f in apply_res.get("failed", [])
                        ]
                    )
                    msg = (
                        f"PATCH APPLICATION REJECTED (Matched: "
                        f"{apply_res.get('matched', 0)}):\n{failed_details}"
                    )
                    console.print(
                        Panel(
                            msg,
                            title="Patch Application Failed",
                            border_style="bold red",
                        )
                    )

                    target_path = os.path.join(
                        self.proj["path"], resolved_target or ""
                    )
                    target_exists = bool(resolved_target) and os.path.exists(target_path)
                    target_lines = 0
                    if target_exists:
                        try:
                            with open(target_path, "r", encoding="utf-8") as tf:
                                target_lines = sum(1 for _ in tf)
                        except Exception:
                            pass

                    state["editor_feedback"] = (
                        f"CRITICAL PATCH ERROR: Your response could not be parsed.\n\n"
                        f"What we found:\n"
                        f"  - Target file: {resolved_target} "
                        f"({'exists' if target_exists else 'does not exist'}, "
                        f"{target_lines} lines)\n"
                        f"  - Expected format: either SEARCH/REPLACE blocks, "
                        f"OR a single full-file code block (>=30 lines) if the "
                        f"whole file must be rewritten.\n\n"
                        f"If your SEARCH block was marked AMBIGUOUS_MATCH, "
                        f"include at least 3-5 surrounding lines so the anchor "
                        f"is unique. A single-token anchor like `/*` will match "
                        f"every comment in the file.\n\n"
                        f"Preferred format for an existing file:\n"
                        f"<<<<<<< SEARCH\n"
                        f"(exact lines to find, copied verbatim from the file)\n"
                        f"=======\n"
                        f"(replacement lines)\n"
                        f">>>>>>> REPLACE\n\n"
                        f"Fallback format for a full rewrite:\n"
                        f"```<language>\n(complete file content)\n```"
                    )

                    state["edit_retries"] = state.get("edit_retries", 0) + 1
                    # Increment iteration_count so MAX_ITERATIONS actually fires
                    state["iteration_count"] = (
                        state.get("iteration_count", 0) + 1
                    )

                    if state["edit_retries"] >= 3:
                        console.print(
                            "[bold red]Max Edit retries reached (3). "
                            "Reverting step to Research.[/bold red]"
                        )
                        # Clean up partial writes to this file if the
                        # pipeline created it. This prevents the researcher
                        # from seeing a broken file on the next cycle.
                        if resolved_target and self._is_pipeline_file(
                            state, resolved_target
                        ):
                            abs_target = os.path.join(
                                self.proj["path"], resolved_target
                            )
                            try:
                                if os.path.exists(abs_target):
                                    os.remove(abs_target)
                                    console.print(
                                        f"[dim]Removed broken pipeline-created "
                                        f"file: {resolved_target}[/dim]"
                                    )
                            except OSError:
                                pass
                            self._untrack_pipeline_file(state, resolved_target)

                        state["current_step"] = "research"
                        state["edit_retries"] = 0
                    else:
                        state["current_step"] = "edit"

                    self.save_state(state)
                    continue

                # ---- Extract the written code ----
                state["edit_retries"] = 0

                research_data = state.get("research_data", {})
                res_file = (
                    state.get("target_file")
                    or research_data.get("target_file")
                    or state.get("active_target_file")
                )

                res_code = ""
                expected_target = state.get("target_file")
                
                if isinstance(raw_generated, dict):
                    res_code = (
                        raw_generated.get("code")
                        or raw_generated.get("content")
                        or raw_generated.get("source")
                        or ""
                    )
                    editor_target = raw_generated.get("target_file")
                    if editor_target:
                        if expected_target and editor_target != expected_target:
                            console.print(
                                f"[bold yellow]WARNING: Editor tried to write '{editor_target}' "
                                f"instead of plan target '{expected_target}'. Forcing plan target.[/bold yellow]"
                            )
                        else:
                            res_file = editor_target
                elif isinstance(raw_generated, str):
                    try:
                        parsed_json = extract_json_payload(raw_generated)
                        if isinstance(parsed_json, dict):
                            res_code = (
                                parsed_json.get("code")
                                or parsed_json.get("content")
                                or parsed_json.get("source")
                                or ""
                            )
                            editor_target = parsed_json.get("target_file")
                            if editor_target:
                                if expected_target and editor_target != expected_target:
                                    console.print(
                                        f"[bold yellow]WARNING: Editor tried to write '{editor_target}' "
                                        f"instead of plan target '{expected_target}'. Forcing plan target.[/bold yellow]"
                                    )
                                else:
                                    res_file = editor_target
                        else:
                            res_code = raw_generated
                    except Exception:
                        fence_match = re.search(
                            r"```(?:\w+)?\n(.*?)```", raw_generated, re.DOTALL
                        )
                        res_code = (
                            fence_match.group(1) if fence_match else raw_generated
                        )

                if not res_file:
                    console.print(
                        "[bold red]Editor Error:[/bold red] No target file specified."
                    )
                    state["current_step"] = "edit"
                    self.save_state(state)
                    continue

                try:
                    res_code = sanitize_generated_code(res_code, allow_patches=True)
                except ValueError as e:
                    console.print(f"[bold red]Editor Error: {str(e)}[/bold red]")
                    state["editor_feedback"] = f"ERROR: {str(e)}"
                    state["current_step"] = "edit"
                    self.save_state(state)
                    continue

                # ---- Security Check ----
                # allow_cleanup = getattr(config, "RSI_MODE", False)
                # danger_hits = find_dangerous_code(res_code, allow_cleanup=allow_cleanup)
                # if danger_hits:
                #     msg = f"Forbidden patterns detected: {', '.join(danger_hits)}"
                #     console.print(
                #         Panel(
                #             msg,
                #             title="Security Warning",
                #             border_style="bold red",
                #         )
                #     )
                #     state["compiler_output"] = f"SECURITY_ERROR: Edit rejected. {msg}"
                #     state["current_step"] = "edit"
                #     self.save_state(state)
                #     continue

                # ---- Backup old content ----
                old_code = self.sandbox.safe_read_file(res_file)
                old_code_backup = (
                    ""
                    if (
                        isinstance(old_code, str)
                        and old_code.startswith("Error")
                    )
                    else str(old_code)
                )
                state["old_code_backup"] = old_code_backup

                # ---- Contract validation ----
                if self.validator is not None:
                    change_set = ChangeSet()
                    change_set.add_write(res_file, res_code)
                    with console.status(
                        "[cyan]Validating against structure index...",
                        spinner="dots",
                    ):
                        report = self.validator.validate_change_set(change_set)

                    if report.internal_error:
                        console.print(
                            f"[dim yellow]Validator internal error (failing open): "
                            f"{report.internal_error}[/dim yellow]"
                        )
                    elif report.errors:
                        failed_lines = "\n".join(
                            f"  {e.path}:{e.line}: [{e.code}] {e.message}"
                            for e in report.errors
                        )
                        console.print(
                            Panel(
                                f"[bold red]CONTRACT VALIDATION FAILED[/bold red]\n"
                                f"{failed_lines}",
                                border_style="bold red",
                            )
                        )
                        state["editor_feedback"] = (
                            "CONTRACT VALIDATION FAILED — your proposed code calls "
                            "names that do not exist in the current codebase:\n"
                            f"{failed_lines}\n\n"
                            "INSTRUCTION: Provide corrections that only reference "
                            "modules, classes, and functions that actually exist. "
                            "If the target symbol must be created first, add it in "
                            "the same patch."
                        )
                        state["edit_retries"] = state.get("edit_retries", 0) + 1
                        state["iteration_count"] = (
                            state.get("iteration_count", 0) + 1
                        )
                        if state["edit_retries"] >= 3:
                            console.print(
                                "[bold red]Max Edit retries reached (3). "
                                "Reverting step to Research.[/bold red]"
                            )
                            state["current_step"] = "research"
                            state["edit_retries"] = 0
                        else:
                            state["current_step"] = "edit"
                        self.save_state(state)
                        continue

                                # --- Pre-flight syntax check ---
                if res_file.endswith(".py"):
                    try:
                        compile(res_code, res_file, "exec")
                    except SyntaxError as syn_err:
                        console.print(f"[bold red]SYNTAX ERROR in generated code: {syn_err}[/bold red]")
                        state["editor_feedback"] = f"SYNTAX ERROR: {syn_err}. Please fix the syntax."
                        state["current_step"] = "edit"
                        state["iteration_count"] = state.get("iteration_count", 0) + 1
                        self.save_state(state)
                        continue

                                # JS / TS pre-flight (second line of defence)
                if res_file.endswith((".js", ".mjs", ".cjs", ".ts", ".tsx")) and res_code:
                    import tempfile
                    import subprocess as _sp
                    _tmp = None
                    try:
                        with tempfile.NamedTemporaryFile(
                            mode="w", suffix=".js", delete=False, encoding="utf-8"
                        ) as _f:
                            _f.write(res_code)
                            _tmp = _f.name
                        _check = _sp.run(
                            ["node", "--check", _tmp],
                            capture_output=True,
                            text=True,
                            timeout=12,
                        )
                        if _check.returncode != 0:
                            err_msg = (_check.stderr or _check.stdout or "").strip()[:500]
                            console.print(f"[bold red]JS SYNTAX ERROR (pre-apply):[/bold red] {err_msg}")
                            state["editor_feedback"] = (
                                f"JS SYNTAX ERROR — your patch would break {res_file}:\n"
                                f"{err_msg}\n\n"
                                f"FIX: count your braces {{ }}, parentheses ( ), and brackets [ ]. "
                                f"Do not insert a new function inside an existing scope. "
                                f"If the file is large, only modify a small, clearly visible section."
                            )
                            state["current_step"] = "edit"
                            state["iteration_count"] = state.get("iteration_count", 0) + 1
                            self.save_state(state)
                            continue
                    except FileNotFoundError:
                        pass  # node not available
                    except Exception as _exc:
                        console.print(f"[dim yellow]JS pre-flight skipped: {_exc}[/dim yellow]")
                    finally:
                        if _tmp and os.path.exists(_tmp):
                            try:
                                os.remove(_tmp)
                            except OSError:
                                pass

                # ---- Atomic apply ----
                change_set = ChangeSet()
                change_set.add_write(res_file, res_code)

                # ---- Atomic apply ----
                change_set = ChangeSet()
                change_set.add_write(res_file, res_code)
                # VERIFICATION FIREWALL: verify the file actually changed on disk
                from tools.security.verification_firewall import (
                    fs_hash, verify_file_changed,
                )

                abs_target = os.path.join(self.proj["path"], res_file)
                pre_apply_hash = fs_hash(abs_target)
                                # --- RSI Critical File Gate (Phase 1.3) ---
                is_rsi = getattr(config, "RSI_MODE", False)
                is_critical = res_file in getattr(config, "RSI_CRITICAL_FILES", set())
                
                if is_rsi and is_critical:
                    pending_dir = os.path.join(self.proj["path"], ".differix", "pending_critical_changes")
                    os.makedirs(pending_dir, exist_ok=True)
                    safe_filename = res_file.replace("/", "_").replace("\\", "_")
                    pending_file = os.path.join(pending_dir, f"{safe_filename}_{int(time.time())}.patch")

                    # Save the proposed change to disk for review
                    with open(pending_file, "w", encoding="utf-8") as f:
                        f.write(res_code)

                    console.print(Panel(
                        f"[bold yellow]RSI CRITICAL FILE GATE TRIGGERED[/bold yellow]\n\n"
                        f"Agent attempted to modify core framework file: [cyan]{res_file}[/cyan]\n"
                        f"Change saved to: [dim]{pending_file}[/dim]\n\n"
                        f"Manual review required. The pipeline will skip applying this file\n"
                        f"and move to the next step.",
                        border_style="yellow",
                    ))
                    
                    state["editor_feedback"] = (
                        f"CHANGE GATED: Modifications to '{res_file}' require human approval. "
                        f"Your code was saved to pending review, but not applied to the live file. "
                        f"Proceed with your next subtask or finish."
                    )
                    
                    # Mark step completed so the pipeline doesn't loop endlessly
                    self._mark_step_completed(state, res_file)
                    
                    # Check if there are more pending steps
                    pending = [s for s in state.get("plan", []) if s.get("status") == "pending"]
                    if pending:
                        state["current_step"] = "research"
                    else:
                        state["current_step"] = "done"
                        state["status"] = "completed"
                        
                    self.save_state(state)
                    continue  # Skip actual application
                # ------------------------------------------

                # ---- Atomic apply ----
                change_set = ChangeSet()
                change_set.add_write(res_file, res_code)
                apply_report = change_set.apply(self.proj["path"])

                if apply_report.ok:
                    changed, post_hash = verify_file_changed(abs_target, pre_apply_hash)
                    if not changed:
                        console.print(
                            f"[bold red]⚠ SECURITY: apply_report.ok=True but file "
                            f"did not change on disk. Trusting fs over agent.[/bold red]"
                        )
                        state["editor_feedback"] = (
                            "Filesystem verification failed: the file did not "
                            "change after apply. The atomic changeset may be corrupted."
                        )
                        state["current_step"] = "edit"
                        self.save_state(state)
                        continue

                if not apply_report.ok:
                    console.print(
                        Panel(
                            f"[bold red]ATOMIC APPLY FAILED[/bold red]\n"
                            f"{apply_report.error}",
                            border_style="bold red",
                        )
                    )
                    state["editor_feedback"] = (
                        f"Filesystem apply failed: {apply_report.error}"
                    )
                    state["current_step"] = "edit"
                    self.save_state(state)
                    continue

                current_diff = self.logger.generate_diff(
                    old_code_backup, res_code, res_file
                )
                console.print(
                    f"[green]Applied {len(apply_report.applied)} file(s) atomically "
                    f"in {apply_report.elapsed_ms:.1f}ms[/green]"
                )

                is_framework_root = os.path.abspath(self.proj["path"]) == os.path.abspath(self.DIFFERIX_FRAMEWORK_ROOT)
                if config.RSI_MODE and is_framework_root:
                    missing = self._check_critical_files_intact()
                    if missing:
                        console.print(Panel(
                            f"[bold red]CRITICAL FILES MISSING AFTER APPLY[/bold red]\n"
                            f"{chr(10).join(missing)}\n\n"
                            f"Rolling back the change set.",
                            border_style="bold red",
                        ))
                        # Revert the file we just wrote
                        if not old_code_backup.strip():
                            abs_path = os.path.join(self.proj["path"], res_file)
                            try:
                                os.remove(abs_path)
                            except OSError:
                                pass
                        else:
                            self.sandbox.safe_write_code(res_file, old_code_backup)

                        state["editor_feedback"] = (
                            f"REJECTED: your change would have deleted critical framework "
                            f"files: {', '.join(missing)}. Rollback applied. Do not touch "
                            f"files outside the target module."
                        )
                        state["current_step"] = "edit"
                        self.save_state(state)
                        continue

                # Track pipeline-created files: if backup is empty, the file
                # did not exist before this write.
                if not old_code_backup.strip():
                    self._track_pipeline_file(state, res_file)

                if self._mark_step_completed(state, res_file):
                    console.print(
                        f"[dim]Marked plan step for '{res_file}' as completed.[/dim]"
                    )

                state["active_target_file"] = res_file
                state["target_file"] = res_file
                state["current_step"] = "review"
                self.save_state(state)

            # ============================================================
            # STEP 3: VERIFICATION & REVIEW
            # ============================================================
            elif step == "review":
                console.rule(
                    "[bold magenta]Step 3: Verification and Review[/bold magenta]"
                )
                if not res_file:
                    res_file = state.get("active_target_file")

                backup_code = state.get("old_code_backup", old_code_backup)

                with console.status(
                    "[magenta]Running compilation/test checks...", spinner="dots"
                ):
                    build_success, log = run_universal_build(
                        self.proj["path"],
                        self.proj.get("lang", "python"),
                        target_file=res_file,
                    )

                # VERIFICATION FIREWALL: build_success from run_universal_build
                # is the ONLY authoritative source. Do not trust state for this.
                from tools.security.verification_firewall import verify_build_result

                claimed_success = bool(state.get("build_success_claimed"))
                verification = verify_build_result(
                    claimed_success=claimed_success,
                    actual_success=build_success,
                    actual_log=log,
                )

                if not verification["consistent"]:
                    for warning in verification["warnings"]:
                        console.print(f"[bold red]⚠ SECURITY: {warning}[/bold red]")
                    # Inconsistency is suspicious but not fatal — we use trusted_success
                    # (the compiler's answer) and log the discrepancy.

                state["compiler_output"] = log  # always compiler, never agent
                state["build_success_claimed"] = None  # clear after verify

                # ---- Coherence gating ----
                if self._coherence_should_run(state):
                    coherence_ctx = self._build_coherence_report(state)
                else:
                    pending_remaining = [
                        s.get("target_file")
                        for s in state.get("plan", [])
                        if isinstance(s, dict) and s.get("status") == "pending"
                    ]
                    coherence_ctx = ""
                    console.print(
                        f"[dim]Coherence check deferred — "
                        f"{len(pending_remaining)} step(s) still pending: "
                        f"{', '.join(str(p) for p in pending_remaining[:3])}"
                        f"{'...' if len(pending_remaining) > 3 else ''}[/dim]"
                    )

                # Coherence gate: if the report has errors, do not hand the diff to
                # the reviewer.
                if coherence_ctx and "error" in coherence_ctx.lower():
                    rerouted = False
                    for step in reversed(state.get("plan", [])):
                        if (
                            isinstance(step, dict)
                            and step.get("target_file", "").endswith((".css", ".html"))
                            and step.get("status") == "completed"
                        ):
                            step["status"] = "pending"
                            state["target_file"] = step["target_file"]
                            state["active_subtask"] = step.get("title")
                            state["current_step"] = "edit"
                            state["editor_feedback"] = (
                                "COHERENCE / SYNTAX FAILURE — the pipeline found "
                                "errors in your code. Fix ONLY what is mentioned below.\n\n"
                                f"--- COMPILER LOG ---\n{log}\n\n"
                                f"--- COHERENCE REPORT ---\n{coherence_ctx}"
                            )
                            console.print(
                                f"[yellow]Coherence failed — rerouting to "
                                f"{step.get('target_file')} to fix consistency[/yellow]"
                            )
                            rerouted = True
                            break

                    if rerouted:
                        if res_file:
                            self._revert_file(state, res_file, backup_code)
                            self._mark_step_pending(state, res_file)

                        state["active_lesson_node_ids"] = []
                        state.pop("active_target_file", None)
                        state.pop("generated_code", None)
                        state.pop("old_code_backup", None)
                        self.save_state(state)
                        continue

                # --- Visual Review Integration ---
                visual_ctx = ""
                if res_file and res_file.endswith(".html"):
                    ref_path = os.path.join(self.proj["path"], "reference.png")
                    target_abs = os.path.join(self.proj["path"], res_file)

                    if os.path.exists(ref_path) and os.path.exists(target_abs):
                        with console.status("[magenta]Running visual diff against reference...", spinner="dots"):
                            try:
                                vis_res = visual_review_page(
                                    html_path=target_abs,
                                    reference_png=ref_path,
                                    output_dir=os.path.join(self.proj["path"], ".differix", "diffs")
                                )
                                if vis_res and not vis_res.get("ok", True):
                                    visual_ctx = vis_res.get("feedback", "")
                                    console.print(f"[bold yellow]Visual Diff failed (SSIM: {vis_res.get('ssim', 0):.2f})[/bold yellow]")
                                elif vis_res and vis_res.get("ssim"):
                                    console.print(f"[dim green]Visual Diff passed (SSIM: {vis_res['ssim']:.2f})[/dim green]")
                            except Exception as vis_exc:
                                console.print(f"[dim yellow]Visual review skipped: {vis_exc}[/dim yellow]")
                # ---- Reviewer ----
                session_ctx = self._build_session_context(
                    state, role="reviewer", record_trigger=True
                )

                with console.status(
                    "[magenta]Reviewer auditing code diff...", spinner="dots"
                ):
                    state, meta = run_reviewer(
                        state,
                        project_dir=self.proj["path"],
                        session_context=session_ctx,
                        coherence_report=coherence_ctx,
                        visual_context=visual_ctx
                    )
                self.update_costs(meta, agent_name="reviewer")

                rev_res = state.get("review_result", {})
                verdict = str(rev_res.get("verdict", "REJECT")).upper()
                reason = (
                    rev_res.get("reason")
                    or rev_res.get("feedback")
                    or "No detailed reason provided."
                )

                # ---- Lesson feedback ----
                injected_ids = state.get("active_lesson_node_ids") or []
                helpful_ids: list = []
                violated_ids: list = []
                if injected_ids:
                    try:
                        helpful_ids, violated_ids = analyze_lesson_outcome(
                            build_success=build_success,
                            build_log=log,
                            injected_node_ids=injected_ids,
                        )
                    except Exception as fb_exc:
                        console.print(
                            f"[dim yellow]Lesson analysis failed: {fb_exc}[/dim yellow]"
                        )

                # ====================================================
                # ACCEPT
                # ====================================================
                if build_success and verdict == "ACCEPT":
                    console.print(
                        Panel(
                            "Build passed and Reviewer accepted changes.",
                            border_style="bold green",
                        )
                    )

                    if helpful_ids:
                        try:
                            n = record_lesson_helpful(helpful_ids)
                            console.print(
                                f"[dim]Lesson feedback: {n} lesson(s) marked "
                                f"helpful (H+1)[/dim]"
                            )
                        except Exception as fb_exc:
                            console.print(
                                f"[dim yellow]record_helpful failed: "
                                f"{fb_exc}[/dim yellow]"
                            )
                    state["active_lesson_node_ids"] = []

                    if self.adr is not None:
                        try:
                            sid = (
                                (state.get("meta") or {}).get("session_id")
                                or "unknown"
                            )
                            entry = build_adr_entry(
                                session_id=sid,
                                cycle=state.get("iteration_count", 0),
                                action="edit",
                                outcome="accepted",
                                files_touched=[res_file] if res_file else [],
                                decisions=[reason[:200]] if reason else [],
                            )
                            self.adr.append(entry)
                        except Exception as adr_exc:
                            console.print(
                                f"[dim yellow]ADR append failed: "
                                f"{adr_exc}[/dim yellow]"
                            )

                    plan = state.get("plan", [])
                    pending_steps = [
                        s
                        for s in plan
                        if isinstance(s, dict) and s.get("status") == "pending"
                    ]

                    if pending_steps:
                        next_step = pending_steps[0]
                        state["active_subtask"] = next_step.get("title")
                        state["target_file"] = next_step.get("target_file")
                        state["current_step"] = "research"
                        console.print(
                            f"[green]Advancing to next step:[/green] "
                            f"{next_step.get('title')}"
                        )
                    else:
                        state["active_subtask"] = None
                        state["target_file"] = None
                        state["current_step"] = "done"
                        state["status"] = "completed"
                        console.print(
                            "[bold green]All plan steps completed.[/bold green]"
                        )

                    state.pop("active_target_file", None)
                    state.pop("generated_code", None)
                    state.pop("old_code_backup", None)
                    state["recent_rejections"] = []

                    state["iteration_count"] = state.get("iteration_count", 0) + 1
                    self.save_state(state)
                    self.logger.record_iteration(state, diff_data=current_diff)

                # ====================================================
                # REJECT
                # ====================================================
                else:
                    console.print(
                        Panel(
                            f"[bold red]REJECTED[/bold red]\n"
                            f"[yellow]Reason:[/yellow] {reason}",
                            border_style="bold red",
                        )
                    )

                    if self._check_rejection_loop(state, reason):
                        console.print(
                            Panel(
                                "[bold red]CIRCUIT BREAKER TRIGGERED[/bold red]\n"
                                "The same rejection reason has appeared 3 times "
                                "in a row. The pipeline is stuck in a loop.\n\n"
                                "Pausing execution. Inspect state.json, fix any "
                                "underlying issue, then run again.",
                                border_style="bold red",
                            )
                        )
                        state["current_step"] = "done"
                        state["status"] = "failed"
                        self.save_state(state)
                        break

                    if violated_ids:
                        try:
                            n = record_lesson_violation(violated_ids)
                            console.print(
                                f"[dim yellow]Lesson feedback: {n} lesson(s) "
                                f"marked as violated (V+1) — the injected rules "
                                f"did not prevent the failure[/dim yellow]"
                            )
                        except Exception as fb_exc:
                            console.print(
                                f"[dim yellow]record_violation failed: "
                                f"{fb_exc}[/dim yellow]"
                            )

                    if self.adr is not None:
                        try:
                            sid = (
                                (state.get("meta") or {}).get("session_id")
                                or "unknown"
                            )
                            entry = build_adr_entry(
                                session_id=sid,
                                cycle=state.get("iteration_count", 0),
                                action="edit",
                                outcome="rejected",
                                files_touched=[res_file] if res_file else [],
                                decisions=[reason[:200]] if reason else [],
                            )
                            self.adr.append(entry)
                        except Exception as adr_exc:
                            console.print(
                                f"[dim yellow]ADR append failed: "
                                f"{adr_exc}[/dim yellow]"
                            )

                    # Revert via helper (handles empty-backup case by deleting)
                    if res_file:
                        self._revert_file(state, res_file, backup_code)
                        self._mark_step_pending(state, res_file)

                    state["compiler_output"] = (
                        f"REVIEWER_REJECTED: {reason}\nBuild output: {log[:300]}"
                    )
                    state["editor_feedback"] = (
                        f"REJECTED BY REVIEWER:\nReason: {reason}"
                    )
                    state["current_step"] = "edit"

                    state.pop("generated_code", None)
                    state.pop("active_target_file", None)
                    state.pop("old_code_backup", None)
                    res_file = None

                    state["iteration_count"] = state.get("iteration_count", 0) + 1
                    self.save_state(state)
                    self.logger.record_iteration(state, diff_data=current_diff)
            elif step == "reflect":
                console.rule("[bold blue]Step 0: Self-Reflection[/bold blue]")

                from tools.reflection import run_reflection

                with console.status(
                    "[blue]Analyzing recent failures for patterns...", spinner="dots"
                ):
                    reflect_result = run_reflection(state, project_dir=self.proj["path"])

                if reflect_result.observations_written:
                    console.print(Panel(
                        f"[bold green]Reflection produced "
                        f"{len(reflect_result.observations_written)} new observation(s):[/bold green]\n"
                        + "\n".join(f"  • {oid}" for oid in reflect_result.observations_written)
                        + (
                            f"\n\n[dim]Skipped (already exist): "
                            f"{len(reflect_result.observations_skipped)}[/dim]"
                            if reflect_result.observations_skipped else ""
                        ),
                        border_style="blue",
                        title="Self-Reflection",
                    ))
                else:
                    console.print(
                        f"[dim]Reflection: no new observations "
                        f"({reflect_result.failures_analyzed} failures analyzed, "
                        f"{reflect_result.patterns_found} patterns found)[/dim]"
                    )

                # Track that reflection ran
                state["metrics"] = state.get("metrics", {})
                state["metrics"]["last_reflection"] = {
                    "iteration": iteration,
                    "observations_written": reflect_result.observations_written,
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }

                # Re-sync lessons into SQLite so new observations become rankable
                try:
                    from tools.lesson_memory_bridge import LessonMemoryBridge
                    bridge = LessonMemoryBridge(
                        lessons_root=os.path.join(self.proj["path"], "lessons")
                    )
                    bridge.sync_all()
                except Exception as exc:
                    console.print(
                        f"[dim yellow]Bridge sync after reflection failed: {exc}[/dim yellow]"
                    )

                # Return to whatever step we came from
                previous = state.pop("_pre_reflect_step", "research")
                state["current_step"] = previous
                self.save_state(state)
                continue
            # ============================================================
            # STEP 4: DONE
            # ============================================================
            elif step in ("done", "completed"):
                console.print(
                    Panel(
                        "[bold green]All tasks completed![/bold green]",
                        border_style="green",
                    )
                )
                break

            else:
                console.print(
                    f"[bold red]Unknown step '{step}'. Defaulting to "
                    f"'research'...[/bold red]"
                )
                state["current_step"] = "research"
                self.save_state(state)
                time.sleep(1)

    # ------------------------------------------------------------------ #
    # Main loop
    # ------------------------------------------------------------------ #
    def start(self):
        console.clear()
        console.print(
            Panel(
                "[bold cyan]DIfferiX Autonomous Multi-Agent Orchestrator[/bold cyan]\n"
                "[dim]Interactive Terminal Interface[/dim]",
                border_style="cyan",
                expand=False,
            )
        )

        if self.proj:
            console.print(
                f"[bold green]Active Workspace:[/bold green] {self.proj['name']} "
                f"([cyan]{self.proj['path']}[/cyan])\n"
            )
            console.print(f"[dim]Framework root: {self.DIFFERIX_FRAMEWORK_ROOT}[/dim]")
            console.print(f"[dim]Registry: {REGISTRY_FILE}[/dim]\n")
        else:
            console.print(
                "[bold yellow]No active workspace selected. "
                "Use 'cd <path>' or 'projects' to pick one.[/bold yellow]\n"
            )

        while True:
            try:
                cmd_line = console.input("[bold cyan]DifferiX > [/bold cyan]").strip()

                if _looks_like_paste_artifact(cmd_line):
                    continue

                if not cmd_line:
                    continue

                parts = cmd_line.split(maxsplit=1)
                head = parts[0].lower()
                sub_args = parts[1] if len(parts) > 1 else ""

                if head in ("task", "t"):
                    self.handle_task_command(sub_args)

                elif head in ("run", "start"):
                    self.run_agent_cycle(prompt_arg=sub_args)

                elif head == "cd":
                    self.change_directory(sub_args)

                elif head in ("projects", "ls"):
                    self.print_projects_table()

                elif head == "llm":
                    if sub_args.startswith("routing "):
                        from tools.llm_selector import set_dynamic_routing_enabled
                        mode = sub_args.split(maxsplit=1)[1].strip().lower()
                        if mode == "on":
                            set_dynamic_routing_enabled(True)
                            console.print("[bold green]Dynamic LLM routing ENABLED. Models will be picked by complexity.[/bold green]")
                        elif mode == "off":
                            set_dynamic_routing_enabled(False)
                            console.print("[bold yellow]Dynamic LLM routing DISABLED. Manual bindings will be used.[/bold yellow]")
                        else:
                            console.print("[bold red]Usage: llm routing [on|off][/bold red]")
                    else:
                        handle_llm_command(sub_args)

                elif head == "msg":
                    project_dir = self.proj["path"] if self.proj else "."
                    handle_msg_command(
                        project_dir=project_dir, state=self.load_state()
                    )

                elif head == "handshake":
                    self.handle_handshake_command(sub_args)

                elif head == "coherence":
                    self.handle_coherence_command(sub_args)

                elif head == "plugin":
                    self.handle_plugin_command(sub_args)

                elif head == "reflect":
                    if not self.proj or not os.path.isdir(self.proj["path"]):
                        console.print("[bold red]Error:[/bold red] No valid workspace selected.")
                        continue
                    state = self.load_state()
                    from tools.reflection import run_reflection
                    with console.status("[blue]Analyzing recent failures...", spinner="dots"):
                        res = run_reflection(state, project_dir=self.proj["path"], force=True)
                    console.print(Panel(
                        json.dumps(res.as_dict(), indent=2),
                        title="Reflection Result",
                        border_style="blue",
                    ))

                elif head == "status":
                    self.show_status()

                elif head in ("log", "changelog"):
                    self.show_changelog()

                elif head == "lesson":
                    self.handle_lesson_command(sub_args)

                elif head == "pause":
                    self.should_pause = True
                    console.print("[yellow]Pause flag set.[/yellow]")

                elif head == "stop":
                    self.should_stop = True
                    console.print("[red]Stop flag set.[/red]")

                elif head == "history":
                    if self.proj:
                        show_all_commits(self.proj["path"])
                    else:
                        console.print(
                            "[bold red]No active project selected.[/bold red]"
                        )

                elif head == "rollback":
                    if not sub_args:
                        console.print(
                            "[bold yellow]Usage:[/bold yellow] rollback <commit_sha>"
                        )
                    elif self.proj:
                        safe_rollback(self.proj["path"], sub_args)

                elif head == "help":
                    self.print_help()

                elif head == "upd-tiers":
                    from tools.model_ranker import auto_update_tiers
                    auto_update_tiers()

                elif head in ("exit", "quit"):
                    console.print("[bold yellow]Exiting DifferiX...[/bold yellow]")
                    sys.exit(0)

                else:
                    console.print(
                        f"[bold red]Unknown command:[/bold red] '{head}'. "
                        f"Type [yellow]help[/yellow] for options."
                    )

            except (KeyboardInterrupt, EOFError):
                console.print("\nExiting DifferiX...")
                break


# ---------------------------------------------------------------------------
# Console-script entry point
# ---------------------------------------------------------------------------
import sys

def run_cli() -> None:
    # Если аргументов нет, просто запускаем интерактивную консоль
    if len(sys.argv) <= 1:
        app = OrchestratorConsole()
        # Инициализируем guard прямо у экземпляра класса, если это необходимо
        app.behavior_guard = BehaviorGuard()  
        app.start()
        return

    cmd = sys.argv[1].lower()
    args = " ".join(sys.argv[2:])

    # Карта соответствия команд и вызываемых функций/методов
    # Для методов создаем один экземпляр OrchestratorConsole()
    console = OrchestratorConsole()
    
    commands = {
        ("--help", "-h", "help"): lambda: console.print_help(),
        ("task", "t", "--task", "-t"): lambda: console.handle_task_command(args),
        ("run", "start", "--run"): lambda: console.run_agent_cycle(prompt_arg=args),
        ("status", "--status"): lambda: console.show_status(),
        ("projects", "ls", "--projects"): lambda: console.print_projects_table(),
        ("log", "changelog", "--log"): lambda: console.show_changelog(),
        ("lesson",): lambda: console.handle_lesson_command(args),
        ("coherence",): lambda: console.handle_coherence_command(args),
    }

    # Поиск и выполнение обычной команды
    for keys, func in commands.items():
        if cmd in keys:
            func()
            return

    # Отдельная обработка для функций, требующих кастомной логики
    if cmd == "llm":
        handle_llm_command(args)
        return

    if cmd == "msg":
        handle_msg_command(
            project_dir=console.proj["path"] if console.proj else ".",
            state=console.load_state(),
        )
        return

    # Если команда не распознана
    print(f"Неизвестная команда: {cmd}. Используйте --help для справки.")

if __name__ == "__main__":
    run_cli()
