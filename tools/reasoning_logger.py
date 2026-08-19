import os
import json
import ast
from datetime import datetime
from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown

console = Console()

AGENT_STYLES = {
    "researcher": {"color": "cyan", "title": "RESEARCHER PLANNING"},
    "editor": {"color": "yellow", "title": "EDITOR CODEGEN"},
    "reviewer": {"color": "magenta", "title": "REVIEWER CRITIC"},
    "system": {"color": "green", "title": "SYSTEM ORCHESTRATOR"}
}

def _sanitize_value(val):
    """Parses stringified Python/JSON lists or dicts back into native types."""
    if isinstance(val, str):
        val_trimmed = val.strip()
        if (val_trimmed.startswith("[") and val_trimmed.endswith("]")) or \
           (val_trimmed.startswith("{") and val_trimmed.endswith("}")):
            try:
                return json.loads(val_trimmed)
            except Exception:
                try:
                    return ast.literal_eval(val_trimmed)
                except Exception:
                    pass
    return val

def log_reasoning(agent_role: str, step: str, reasoning_data: dict, project_root: str = "."):
    status_dir = os.path.join(project_root, "status")
    os.makedirs(status_dir, exist_ok=True)

    jsonl_path = os.path.join(status_dir, "reasoning_log.jsonl")
    md_path = os.path.join(status_dir, "last_thoughts.md")
    timestamp = datetime.now().isoformat()

    # Clean empty values & sanitize stringified blobs
    clean_data = {}
    for k, v in reasoning_data.items():
        if v not in (None, "", [], {}):
            clean_data[k] = _sanitize_value(v)

    role_key = agent_role.lower()
    style_info = AGENT_STYLES.get(role_key, {"color": "white", "title": agent_role.upper()})
    
    formatted_body = ""
    for key, val in clean_data.items():
        field_name = key.replace("_", " ").title()
        if isinstance(val, (dict, list)):
            val_str = json.dumps(val, ensure_ascii=False, indent=2)
            formatted_body += f"**{field_name}:**\n```json\n{val_str}\n```\n\n"
        else:
            formatted_body += f"**{field_name}:**\n{val}\n\n"

    panel = Panel(
        Markdown(formatted_body.strip() if formatted_body else "*No detailed payload provided.*"),
        title=f"[{style_info['color']} bold]{style_info['title']} | Step: {step}[/]",
        subtitle=f"[dim]{timestamp}[/dim]",
        border_style=style_info["color"],
        padding=(1, 2)
    )
    console.print(panel)

    record = {
        "timestamp": timestamp,
        "agent": agent_role,
        "step": step,
        "data": clean_data
    }
    with open(jsonl_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# Agent Thought Log\n\n- **Agent:** `{agent_role.upper()}`\n- **Step:** `{step}`\n\n" + formatted_body)