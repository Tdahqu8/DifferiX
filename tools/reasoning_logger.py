import os
import json
from datetime import datetime

def log_reasoning(agent_role: str, step: str, reasoning_data: dict, project_root: str = "."):
    """
    Appends agent reasoning to status/last_thoughts.md and status/reasoning_log.jsonl,
    and prints formatted thought streams directly to the terminal output.
    """
    status_dir = os.path.join(project_root, "status")
    os.makedirs(status_dir, exist_ok=True)

    md_path = os.path.join(status_dir, "last_thoughts.md")
    jsonl_path = os.path.join(status_dir, "reasoning_log.jsonl")

    timestamp = datetime.now().isoformat()

    # 1. Terminal / Console Streaming Output
    title_bar = f"[{agent_role.upper()} THOUGHTS & REASONING | Step: {step}]"
    divider = "=" * max(len(title_bar), 60)
    
    print(f"\n{divider}\n{title_bar}\n{divider}")
    for key, val in reasoning_data.items():
        if val:
            field_name = key.replace("_", " ").title()
            print(f"\n► {field_name}:")
            if isinstance(val, (dict, list)):
                print(json.dumps(val, ensure_ascii=False, indent=2))
            else:
                print(f"  {val}")
    print(f"{divider}\n")

    # 2. Append JSONL Record
    record = {
        "timestamp": timestamp,
        "agent": agent_role,
        "step": step,
        "data": reasoning_data
    }
    with open(jsonl_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    # 3. Update status/last_thoughts.md Summary
    md_content = f"# Agent Thought Log\n\n"
    md_content += f"- **Timestamp:** `{timestamp}`\n"
    md_content += f"- **Agent Role:** `{agent_role.upper()}`\n"
    md_content += f"- **Step:** `{step}`\n\n"
    
    for key, val in reasoning_data.items():
        if val:
            field_name = key.replace("_", " ").title()
            if isinstance(val, (dict, list)):
                val_str = json.dumps(val, ensure_ascii=False, indent=2)
                md_content += f"### {field_name}\n```json\n{val_str}\n```\n\n"
            else:
                md_content += f"### {field_name}\n{val}\n\n"

    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_content)