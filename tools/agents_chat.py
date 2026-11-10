# tools/agents_chat.py
import sys
import json
import os
import config
from tools.llm_client import safe_chat_completion
from tools.lesson_manager import (
    get_promoted_lessons_prompt,
    get_observations_prompt,
    get_candidates_prompt,
    get_deprecated_prompt
)
from tools.skill_manager import get_combined_agent_skill

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.prompt import Prompt
    console = Console()
    HAS_RICH = True
except ImportError:
    console = None
    HAS_RICH = False

AGENTS = {
    "1": ("Researcher", "researcher"),
    "2": ("Editor", "editor"),
    "3": ("Reviewer", "reviewer"),
}

# Framework internal files that must never leak into workspace trees
FRAMEWORK_EXCLUSIONS = {
    ".env", "config.py", "state.json", "agents_chat.py", "agents_chat_2.py",
    "deduplicate.py", ".git", "__pycache__", ".pytest_cache", "node_modules", ".venv", "venv"
}

def get_project_context(project_dir="."):
    """Scans target project directory while keeping internal framework files hidden."""
    files_list = []
    base_path = os.path.abspath(project_dir)
    for root, dirs, files in os.walk(base_path):
        dirs[:] = [d for d in dirs if d not in FRAMEWORK_EXCLUSIONS]
        for f in files:
            if f in FRAMEWORK_EXCLUSIONS or f.endswith(".pyc"):
                continue
            rel_path = os.path.relpath(os.path.join(root, f), base_path)
            files_list.append(rel_path)
    return "\n".join(files_list[:100]) or "Empty project directory."

def get_iteration_logs_prompt(state=None):
    """Retrieves standard iteration execution logs if available."""
    if state and "iteration_logs" in state:
        logs = state.get("iteration_logs", [])
        if isinstance(logs, list):
            return "\n".join([f"- {log}" for log in logs[-10:]])
        return str(logs)
    return "No iteration logs recorded."

def handle_msg_command(project_dir=".", state=None):
    state = state or {}
    if HAS_RICH:
        console.print(Panel("[bold cyan]VegeX Interactive Agent Terminal[/bold cyan]", border_style="cyan"))
        console.print("Select an agent to communicate with:")
        console.print(" [1] Researcher (Super-User: Full Lesson & Memory Access)\n [2] Editor (Consumer: Read-Only Promoted Lessons)\n [3] Reviewer (Consumer: Read-Only Promoted Lessons)")
        choice = Prompt.ask("Select agent", choices=["1", "2", "3"], default="1")
    else:
        print("\n--- VegeX Interactive Agent Terminal ---")
        print("Select an agent:\n [1] Researcher (Super User)\n [2] Editor\n [3] Reviewer")
        choice = input("Select agent (1-3) [default 1]: ").strip() or "1"
        if choice not in AGENTS:
            print("[!] Invalid agent selected.")
            return

    agent_name, role_key = AGENTS[choice]
    # Динамическое получение активной модели из config
    model_name = config.get_active_model(role_key)
    
    # Dynamically load the combined role-specific common rule (.md) and domain skill
    skill_prompt = get_combined_agent_skill(role_key, project_dir)
    project_files = get_project_context(project_dir)
    iteration_logs = get_iteration_logs_prompt(state)

    if choice == "1":
        # Researcher super-user access
        lessons_context = (
            f"--- PROMOTED LESSONS ---\n{get_promoted_lessons_prompt()}\n\n"
            f"--- CANDIDATES ---\n{get_candidates_prompt()}\n\n"
            f"--- RAW OBSERVATIONS ---\n{get_observations_prompt()}\n\n"
            f"--- DEPRECATED ---\n{get_deprecated_prompt()}"
        )
        role_instruction = (
            "You are the LESSON SUPER-USER & ARCHITECT. You have access to all memory lifecycle stages. "
            "You may observe, propose candidate rules, refactor lessons, and analyze root causes."
        )
    else:
        # Editor / Reviewer consumer access
        lessons_context = f"--- PROMOTED LESSONS ONLY ---\n{get_promoted_lessons_prompt()}"
        role_instruction = (
            "You are a LESSON CONSUMER. You have read-only access to promoted lessons. "
            "You CANNOT inspect raw observations or manage system memory directly."
        )

    system_prompt = (
        f"{skill_prompt}\n\n"
        f"=== WORKSPACE LESSONS & MEMORY ===\n{lessons_context}\n\n"
        f"=== ITERATION EXECUTION LOGS ===\n{iteration_logs}\n\n"
        f"=== SANDBOXED PROJECT TREE ({project_dir}) ===\n{project_files}\n\n"
        f"INSTRUCTION: {role_instruction}\n"
        "SECURITY RULE: Never attempt to scan or inspect files outside the target project folder."
    )
    messages = [{"role": "system", "content": system_prompt}]

    if HAS_RICH:
        console.print(f"\n[bold green]Connected to {agent_name} ({model_name}). Type 'exit' or 'back' to return.[/bold green]\n")
    else:
        print(f"\n[+] Connected to {agent_name} ({model_name}). Type 'exit' or 'back' to return.\n")

    while True:
        try:
            user_input = Prompt.ask(f"[bold yellow]You -> {agent_name}[/bold yellow]").strip() if HAS_RICH else input(f"You -> {agent_name}: ").strip()

            if not user_input:
                continue
            if user_input.lower() in ["exit", "back", "quit"]:
                print("[+] Exiting chat session...\n")
                break

            messages.append({"role": "user", "content": user_input})

            response, provider = safe_chat_completion(
                role_or_model=model_name,
                messages=messages,
                temperature=0.3
            )

            reply = response.choices[0].message.content.strip()
            messages.append({"role": "assistant", "content": reply})

            if HAS_RICH:
                console.print(Panel(reply, title=f"{agent_name} ({provider})", border_style="bold blue"))
            else:
                print(f"\n{agent_name} ({provider}):\n{reply}\n")

        except (KeyboardInterrupt, EOFError):
            print("\n[+] Session terminated.")
            break

def start_chat():
    handle_msg_command()

if __name__ == "__main__":
    start_chat()