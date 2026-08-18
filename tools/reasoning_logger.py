import os
import json
from datetime import datetime
from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown

console = Console()

# Distinct color schemes per agent role
AGENT_STYLES = {
    "researcher": {"color": "cyan", "title": "RESEARCHER PLANNING"},
    "editor": {"color": "yellow", "title": "EDITOR CODEGEN"},
    "reviewer": {"color": "magenta", "title": "REVIEWER CRITIC"},
    "system": {"color": "green", "title": "SYSTEM ORCHESTRATOR"}
}

def log_reasoning(agent_role: str, step: str, reasoning_data: dict, project_root: str = "."):
    """
    Renders styled agent thoughts in terminal panels and saves log records.
    """
    status_dir = os.path.join(project_root, "status")
    os.makedirs(status_dir, exist_ok=True)

    jsonl_path = os.path.join(status_dir, "reasoning_log.jsonl")
    md_path = os.path.join(status_dir, "last_thoughts.md")
    timestamp = datetime.now().isoformat()

    # 1. Format and print Rich Terminal Panel
    role_key = agent_role.lower()
    style_info = AGENT_STYLES.get(role_key, {"color": "white", "title": agent_role.upper()})
    
    formatted_body = ""
    for key, val in reasoning_data.items():
        if val:
            field_name = key.replace("_", " ").title()
            if isinstance(val, (dict, list)):
                val_str = json.dumps(val, ensure_ascii=False, indent=2)
                formatted_body += f"**{field_name}:**\n```json\n{val_str}\n```\n\n"
            else:
                formatted_body += f"**{field_name}:**\n{val}\n\n"

    panel = Panel(
        Markdown(formatted_body.strip()),
        title=f"[{style_info['color']} bold]{style_info['title']} | Step: {step}[/]",
        subtitle=f"[dim]{timestamp}[/dim]",
        border_style=style_info["color"],
        padding=(1, 2)
    )
    console.print(panel)

    # 2. Append JSONL Record
    record = {
        "timestamp": timestamp,
        "agent": agent_role,
        "step": step,
        "data": reasoning_data
    }
    with open(jsonl_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    # 3. Update status/last_thoughts.md
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# Agent Thought Log\n\n- **Agent:** `{agent_role.upper()}`\n- **Step:** `{step}`\n\n" + formatted_body)