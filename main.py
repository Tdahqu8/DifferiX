import json
import os
import sys
from datetime import datetime
from dotenv import load_dotenv

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.markdown import Markdown

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
from tools.changelog_engine import ChangelogEngine
from tools.project_scanner import get_project_files
from tools.project_structure import generate_or_update_structure
from tools.security_filter import find_dangerous_code
from tools.reasoning_logger import log_reasoning

from agents.researcher import run_researcher
from agents.editor import run_editor
from agents.reviewer import run_reviewer

load_dotenv()


class OrchestratorConsole:
    def __init__(self):
        self.total_cost = 0.0
        self.should_pause = False
        self.should_stop = False
        self.proj = None
        self.sandbox = None
        self.logger = None

    def update_costs(self, meta):
        cost = (meta.prompt_token_count * (config.PRICE_INPUT_1M / 1_000_000)) + (
            meta.candidates_token_count * (config.PRICE_OUTPUT_1M / 1_000_000)
        )
        self.total_cost += cost
        
        table = Table(show_header=True, header_style="bold blue", box=None)
        table.add_column("Input Tokens", justify="right")
        table.add_column("Output Tokens", justify="right")
        table.add_column("Step Cost", justify="right", style="green")
        table.add_column("Session Total", justify="right", style="bold green")
        
        table.add_row(
            f"{meta.prompt_token_count:,}",
            f"{meta.candidates_token_count:,}",
            f"${cost:.5f}",
            f"${self.total_cost:.5f}"
        )
        console.print(table)

    def load_state(self):
        with open("state.json", "r", encoding="utf-8") as f:
            return json.load(f)

    def save_state(self, state):
        with open("state.json", "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)

    def print_help(self):
        help_text = """
[bold cyan]Available Commands:[/bold cyan]
  [yellow]run / start[/yellow]         - Execute the main agent iteration cycle
  [yellow]pause[/yellow]               - Pause execution and view recent changelogs
  [yellow]stop[/yellow]                - Halt the active agent cycle
  [yellow]status[/yellow]              - Display current workflow state and system metrics
  [yellow]log / changelog[/yellow]     - Render session changelog summaries
  [yellow]warn low|mid|high[/yellow]   - Issue an explicit warning instruction to agents
  [yellow]rollback[/yellow]            - Revert repository to a target Git commit SHA
  [yellow]history / commits[/yellow]   - Display full version control commit log
  [yellow]help[/yellow]                - Show command directory
  [yellow]exit / quit[/yellow]         - Terminate the orchestrator session
"""
        console.print(Panel(help_text.strip(), title="System Help", border_style="cyan"))

    def show_status(self):
        try:
            state = self.load_state()
            table = Table(title="System Status", show_header=False, border_style="blue")
            table.add_column("Property", style="bold yellow")
            table.add_column("Value", style="white")

            table.add_row("Current Step", str(state.get("current_step", "N/A")).upper())
            table.add_row("Iteration Count", str(state.get("iteration_count", 0)))
            table.add_row("Active Goal", str(state.get("goal", "N/A")))
            table.add_row("Human Assistance Required", str(state.get("need_human", False)))
            table.add_row("Active Warnings", str(len(state.get("warnings", []))))
            
            compiler_out = str(state.get("compiler_output", "N/A"))
            if len(compiler_out) > 120:
                compiler_out = compiler_out[:120] + "..."
            table.add_row("Last Compiler Output", compiler_out)

            console.print(table)
        except Exception as e:
            console.print(f"[bold red]Failed to load state file:[/bold red] {e}")

    def show_changelog(self):
        log_dir = "./iteration_logs"
        if not os.path.exists(log_dir):
            console.print("[bold red]Error:[/bold red] The directory 'iteration_logs' does not exist.")
            return

        files = sorted(
            [f for f in os.listdir(log_dir) if f.endswith("_SUMMARY.md")],
            reverse=True,
        )
        if not files:
            console.print("[bold yellow]Notice:[/bold yellow] No summary logs found in directory.")
            return

        latest = files[0]
        path = os.path.join(log_dir, latest)
        
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
            if len(content) > 4000:
                content = content[:4000] + "\n\n...[content truncated]"

        console.print(Panel(Markdown(content), title=f"Changelog: {latest}", border_style="magenta"))

    def run_agent_cycle(self):
        if not self.proj or not self.sandbox or not self.logger:
            console.print("[bold red]Error:[/bold red] No project selected. Please select a project first.")
            return

        console.print(Panel("[bold green]Starting multi-agent execution pipeline...[/bold green]", border_style="green"))
        current_diff = None
        self.should_pause = False
        self.should_stop = False

        with console.status("[bold blue]Updating project file structure tree...", spinner="dots"):
            struct_res = generate_or_update_structure(self.proj["path"])
        console.print(f"[green]Project structure synchronized.[/green] Total tracked files: [yellow]{struct_res['file_count']}[/yellow]")

        while True:
            if self.should_stop:
                console.print("[bold red]Execution stopped by operator.[/bold red]")
                self.should_stop = False
                break

            if self.should_pause:
                console.print("[bold yellow]Execution paused by operator. Displaying recent changelog...[/bold yellow]")
                self.show_changelog()
                self.should_pause = False
                break

            state = self.load_state()
            project_files = get_project_files(self.proj["path"])

            if (
                state.get("iteration_count", 0) >= config.MAX_ITERATIONS
                or self.total_cost >= config.MAX_BUDGET_USD
            ):
                console.print(
                    Panel(
                        "[bold red]Resource Threshold Reached![/bold red]\n"
                        "Execution paused to protect iteration limits and token budget.",
                        border_style="red"
                    )
                )
                cmd = console.input("[yellow]Select Action ([C]ontinue / [S]top): [/yellow]").strip().lower()
                if cmd == "c":
                    state["iteration_count"] = 0
                    self.save_state(state)
                    continue
                else:
                    break

            # ----------------- 1. RESEARCH -----------------
            if state.get("current_step") == "research":
                console.rule("[bold cyan]Step 1: Research and Planning[/bold cyan]")
                
                with console.status("[cyan]Researcher agent generating technical plan...", spinner="dots"):
                    state, meta = run_researcher(state, self.proj["name"], project_files)
                
                self.update_costs(meta)

                research_data = state.get("research_data", {})
                log_reasoning("researcher", "research", {
                    "analysis": research_data.get("analysis") if isinstance(research_data, dict) else str(research_data),
                    "plan": research_data.get("plan") if isinstance(research_data, dict) else []
                }, project_root=self.proj["path"])

                self.save_state(state)

            # ----------------- 2. EDIT -----------------
            elif state.get("current_step") == "edit":
                console.rule("[bold yellow]Step 2: Code Generation[/bold yellow]")
                
                try:
                    with console.status("[yellow]Editor agent writing source code...", spinner="dots"):
                        state, meta = run_editor(state, project_files)
                    self.update_costs(meta)
                except Exception as e:
                    console.print(f"[bold red]Editor Execution Error:[/bold red] {e}")
                    state["iteration_count"] = state.get("iteration_count", 0) + 1
                    self.save_state(state)
                    continue

                generated = state.get("generated_code", {})
                res_file = generated.get("target_file")
                res_code = generated.get("code", "")

                if isinstance(res_code, dict):
                    res_code = res_code.get("content") or res_code.get("code") or json.dumps(res_code, ensure_ascii=False)
                elif isinstance(res_code, list):
                    res_code = "\n".join(str(x) for x in res_code)
                else:
                    res_code = str(res_code or "")

                if not res_file:
                    console.print("[bold red]Editor Error:[/bold red] No target file specified in output.")
                    self.save_state(state)
                    continue

                log_reasoning("editor", "edit", {
                    "target_file": res_file,
                    "why": generated.get("why", "Planned edits implementation"),
                    "changes_summary": generated.get("changes_summary", "")
                }, project_root=self.proj["path"])

                danger_hits = find_dangerous_code(res_code)
                if danger_hits:
                    msg = f"Forbidden security patterns detected: {', '.join(danger_hits)}"
                    console.print(Panel(msg, title="Security Interceptor Warning", border_style="bold red"))
                    state["compiler_output"] = f"SECURITY_ERROR: Edit rejected. {msg}"
                    state["current_step"] = "review"
                    self.save_state(state)
                    continue

                old_code = self.sandbox.safe_read_file(res_file)
                if isinstance(old_code, str) and old_code.startswith("ERROR"):
                    old_code = ""

                current_diff = self.logger.generate_diff(str(old_code), res_code, res_file)
                write_result = self.sandbox.safe_write_code(res_file, res_code)
                console.print(f"[green]File Write Result:[/green] {write_result}")

                state["current_step"] = "review"
                self.save_state(state)

            # ----------------- 3. REVIEW -----------------
            elif state.get("current_step") == "review":
                console.rule("[bold magenta]Step 3: Build Verification and Review[/bold magenta]")
                
                with console.status("[magenta]Running universal test environment runner...", spinner="dots"):
                    success, log = run_universal_build(self.proj["path"], self.proj["lang"])
                
                state["compiler_output"] = log

                if success:
                    console.print(Panel(log, title="Build/Test Log - Success", border_style="bold green"))
                    
                    # 1. Update plan step statuses
                    plan = state.get("plan", [])
                    step_updated = False
                    for step_item in plan:
                        if isinstance(step_item, dict) and step_item.get("status") == "pending":
                            step_item["status"] = "completed"
                            step_updated = True
                            console.print(f"[bold green]Plan step completed:[/bold green] {step_item.get('title', 'Subtask')}")
                            break

                    # 2. Check if all plan steps are finished
                    pending_steps = [s for s in plan if isinstance(s, dict) and s.get("status") == "pending"]

                    if pending_steps:
                        # Loop back to research/edit for remaining plan items
                        console.print(f"[bold yellow]Build passed, but {len(pending_steps)} plan step(s) remain. Continuing cycle...[/bold yellow]")
                        state["current_step"] = "research"
                        self.save_state(state)
                        
                        try:
                            repo = get_repo()
                            make_auto_commit(repo, "orchestrator", f"Progress checkpoint: {len(pending_steps)} steps remaining")
                        except Exception as e:
                            console.print(f"[bold red]Git Checkpoint Error:[/bold red] {e}")

                        self.logger.record_iteration(state, diff_data=current_diff)
                    else:
                        # Fully finished only when no pending plan items remain
                        state["current_step"] = "done"
                        self.save_state(state)

                        try:
                            repo = get_repo()
                            make_auto_commit(repo, "orchestrator", "Success after debugging")
                            create_success_tag(repo)
                        except Exception as e:
                            console.print(f"[bold red]Git Checkpoint Error:[/bold red] {e}")

                        self.logger.record_iteration(state, diff_data=current_diff)
                        console.print(Panel("[bold green]All workflow steps completed successfully.[/bold green]", border_style="green"))
                        break
                else:
                    console.print(Panel(log, title="Build/Test Log - Failed", border_style="bold red"))
                    
                    with console.status("[magenta]Reviewer agent diagnosing failure...", spinner="dots"):
                        state, meta = run_reviewer(state)
                    
                    self.update_costs(meta)

                    rev_res = state.get("review_result", {})
                    log_reasoning("reviewer", "review", {
                        "verdict": rev_res.get("verdict"),
                        "root_cause": rev_res.get("root_cause"),
                        "fix_instruction": rev_res.get("fix_instruction"),
                        "reasoning_summary": rev_res.get("reasoning_summary")
                    }, project_root=self.proj["path"])

                    # If review fails, keep step in edit mode for retry
                    state["current_step"] = "edit"
                    self.save_state(state)
                    self.logger.record_iteration(state, diff_data=current_diff)

            else:
                console.print(f"[bold yellow]Pipeline finished or invalid step encountered:[/bold yellow] {state.get('current_step')}")
                break

    def start(self):
        console.clear()
        console.print(
            Panel(
                "[bold cyan]f(x) Autonomous Multi-Agent Orchestrator[/bold cyan]\n"
                "[dim]Interactive Terminal Interface[/dim]",
                border_style="cyan",
                expand=False
            )
        )
        
        table = Table(title="Available Projects", show_header=True, header_style="bold green")
        table.add_column("Key", justify="center", style="bold yellow")
        table.add_column("Project Name", style="white")
        table.add_column("Target Language", style="cyan")

        for k, v in config.PROJECTS.items():
            table.add_row(k, v["name"], v["lang"])

        console.print(table)

        choice = console.input("\n[bold yellow]Select project key: [/bold yellow]").strip()
        if choice not in config.PROJECTS:
            console.print("[bold red]Invalid selection. Terminating session.[/bold red]")
            return

        self.proj = config.PROJECTS[choice]
        self.sandbox = FolderSandbox(self.proj["path"])
        self.logger = ChangelogEngine()

        console.print(f"[green]Active project selected:[/green] [bold white]{self.proj['name']}[/bold white]\n")

        while True:
            cmd_line = console.input("[bold cyan]f(x)> [/bold cyan]").strip()
            if not cmd_line:
                continue

            parts = cmd_line.split(maxsplit=1)
            head = parts[0].lower()

            if head in ("exit", "quit"):
                console.print("[bold yellow]Session terminated by user.[/bold yellow]")
                break
            elif head == "help":
                self.print_help()
            elif head in ("run", "start"):
                self.run_agent_cycle()
            elif head == "status":
                self.show_status()
            elif head in ("log", "changelog"):
                self.show_changelog()
            else:
                console.print("[bold red]Unknown command.[/bold red] Type [yellow]help[/yellow] to view available operations.")

if __name__ == "__main__":
    OrchestratorConsole().start()