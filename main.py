import json
import os
import sys
from datetime import datetime

import config
from dotenv import load_dotenv

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
        print(
            f"[Тokens per step]: Input: {meta.prompt_token_count} | "
            f"Output: {meta.candidates_token_count} | Cost: ${cost:.5f}"
        )
        print(f"[Overall session bill]: ${self.total_cost:.5f}")

    def load_state(self):
        with open("state.json", "r", encoding="utf-8") as f:
            return json.load(f)

    def save_state(self, state):
        with open("state.json", "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)

    def print_help(self):
        print(
            """
Available commands:
    run / start         — run agent cycle
    pause               — break cycle + show changelog
    stop                — stop cycle
    status              — current status
    log / changelog     — show current session log
    warn low|mid|high   — warn an agent
    rollback            — rollback to selected git commit
    history / commits   — all git commits list
    help                — all availabe commands
    exit / quit         — exit from f(x) terminal
"""
        )

    def show_status(self):
        try:
            state = self.load_state()
            print("\n--- STATUS ---")
            print(f"Current step:  {state.get('current_step')}")
            print(f"Iteration:     {state.get('iteration_count')}")
            print(f"Goal:          {str(state.get('goal', '—'))[:80]}")
            print(f"Last build:    {str(state.get('compiler_output', '—'))[:120]}")
            print(f"need_human:    {state.get('need_human', False)}")
            print(f"Warnings:      {len(state.get('warnings', []))}")
            print("---------------")
        except Exception as e:
            print(f"Failed to read the state: {e}")

    def show_changelog(self):
        log_dir = "./iteration_logs"
        if not os.path.exists(log_dir):
            print("Error: iteration_logs blank.")
            return

        files = sorted(
            [f for f in os.listdir(log_dir) if f.endswith("_SUMMARY.md")],
            reverse=True,
        )
        if not files:
            print("No summary files.")
            return

        latest = files[0]
        path = os.path.join(log_dir, latest)
        print(f"\n--- Changelog: {latest} ---")
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
            print(content[:4000])
            if len(content) > 4000:
                print("\n... (cut)")
        print("--- end ---")

    def run_agent_cycle(self):
        if not self.proj or not self.sandbox or not self.logger:
            print("Project wasn't chosen")
            return

        print("\nRunning agent cycle...")
        current_diff = None
        self.should_pause = False
        self.should_stop = False

        struct_res = generate_or_update_structure(self.proj["path"])
        print(f"Project structure updated. (Files: {struct_res['file_count']})")

        while True:
            if self.should_stop:
                print("⏹ Cycle stopped by the user.")
                self.should_stop = False
                break

            if self.should_pause:
                print("⏸ CYcle paused. Showing changelog:")
                self.show_changelog()
                self.should_pause = False
                break

            state = self.load_state()
            project_files = get_project_files(self.proj["path"])

            if (
                state.get("iteration_count", 0) >= config.MAX_ITERATIONS
                or self.total_cost >= config.MAX_BUDGET_USD
            ):
                print("\n The agents have stopped their workflow to save tokens.")
                cmd = input("\n Command ([C]continue / [S]stop / [Enter] exit): ").strip().lower()
                if cmd == "c":
                    state["iteration_count"] = 0
                    self.save_state(state)
                    continue
                else:
                    break

            # ----------------- 1. RESEARCH -----------------
            if state.get("current_step") == "research":
                print("\n• [Step No. 1]: Researcher build the plan...")
                state, meta = run_researcher(state, self.proj["name"], project_files)
                self.update_costs(meta)

                # Логируем мысли Исследователя
                research_data = state.get("research_data", {})
                log_reasoning("researcher", "research", {
                    "analysis": research_data.get("analysis") if isinstance(research_data, dict) else str(research_data),
                    "plan": research_data.get("plan") if isinstance(research_data, dict) else []
                }, project_root=self.proj["path"])

                self.save_state(state)

            # ----------------- 2: EDIT -----------------
            elif state.get("current_step") == "edit":
                print("\n• [Step 2]: Editor writing code...")
                try:
                    state, meta = run_editor(state, project_files)
                    self.update_costs(meta)
                except Exception as e:
                    print(f"Editor error: {e}")
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
                    print("Editor haven't chosed target_file")
                    self.save_state(state)
                    continue

                # Логируем мысли Редактора
                log_reasoning("editor", "edit", {
                    "target_file": res_file,
                    "why": generated.get("why", "Planned edits implementation"),
                    "changes_summary": generated.get("changes_summary", "")
                }, project_root=self.proj["path"])

                danger_hits = find_dangerous_code(res_code)
                if danger_hits:
                    print(f"[SECURITY BLOCK]: Dangerous code patterns detected: {danger_hits}")
                    state["compiler_output"] = f"SECURITY_ERROR: Edit rejected. Dangerous code: {', '.join(danger_hits)}"
                    state["current_step"] = "review"
                    self.save_state(state)
                    continue

                old_code = self.sandbox.safe_read_file(res_file)
                if isinstance(old_code, str) and old_code.startswith("ERROR"):
                    old_code = ""

                current_diff = self.logger.generate_diff(str(old_code), res_code, res_file)
                print(self.sandbox.safe_write_code(res_file, res_code))

                state["current_step"] = "review"
                self.save_state(state)

            # ----------------- 3: REVIEW -----------------
            elif state.get("current_step") == "review":
                print("\n• [Шаг 3]: Compilation and reviewer's review...")
                success, log = run_universal_build(self.proj["path"], self.proj["lang"])
                state["compiler_output"] = log

                if success:
                    print(f"Code built successfully.\n{log}")
                    state["current_step"] = "done"
                    self.save_state(state)

                    try:
                        repo = get_repo()
                        make_auto_commit(repo, "orchestrator", "Success after debugging")
                        create_success_tag(repo)
                    except Exception as e:
                        print(f"Failed to create success-commit: {e}")

                    self.logger.record_iteration(state, diff_data=current_diff)
                    print("\nAgents have finished their job.")
                    break
                else:
                    state, meta = run_reviewer(state)
                    self.update_costs(meta)

                    rev_res = state.get("review_result", {})
                    log_reasoning("reviewer", "review", {
                        "verdict": rev_res.get("verdict"),
                        "root_cause": rev_res.get("root_cause"),
                        "fix_instruction": rev_res.get("fix_instruction"),
                        "reasoning_summary": rev_res.get("reasoning_summary")
                    }, project_root=self.proj["path"])

                    self.save_state(state)
                    self.logger.record_iteration(state, diff_data=current_diff)

            else:
                print(f"Step finished or unknown: {state.get('current_step')}.")
                break

    def start(self):
        print("====== f(x) Orchestrator ======")
        print("Select a project:")
        for k, v in config.PROJECTS.items():
            print(f"{k}) {v['name']} ({v['lang']})")

        choice = input("Write project number: ").strip()
        if choice not in config.PROJECTS:
            print("Exit.")
            return

        self.proj = config.PROJECTS[choice]
        self.sandbox = FolderSandbox(self.proj["path"])
        self.logger = ChangelogEngine()

        while True:
            cmd_line = input("f(x)> ").strip()
            if not cmd_line:
                continue

            parts = cmd_line.split(maxsplit=1)
            head = parts[0].lower()

            if head in ("exit", "quit"):
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
                print("Unknown command. Please write help.")

if __name__ == "__main__":
    OrchestratorConsole().start()