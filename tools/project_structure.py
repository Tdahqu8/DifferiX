import os
import json
import hashlib
from datetime import datetime

def _compute_fingerprint(file_list: list[dict]) -> str:
    sorted_files = sorted(file_list, key=lambda x: x['path'])
    hasher = hashlib.sha256()
    for f in sorted_files:
        entry_str = f"{f['path']}|{f['size']}|{f['mtime']}"
        hasher.update(entry_str.encode('utf-8'))
    return hasher.hexdigest()

def _scan_project(project_path: str) -> list[dict]:
    ignore_dirs = {
        '.git', '__pycache__', '.vs', 'bin', 'obj', 'node_modules', 
        'iteration_logs', 'AI_Orchestrator_Backups', '.vscode'
    }
    ignore_files = {
        '.env', 'PROJECT_STRUCTURE.md', 'structure_meta.json'
    }
    
    entries = []
    project_path = os.path.abspath(project_path)
    
    for root, dirs, files in os.walk(project_path):
        dirs[:] = [d for d in dirs if d not in ignore_dirs]
        
        for file in files:
            if file in ignore_files:
                continue
            
            full_path = os.path.join(root, file)
            rel_path = os.path.relpath(full_path, project_path).replace('\\', '/')
            
            try:
                stat = os.stat(full_path)
                entries.append({
                    'path': rel_path,
                    'size': stat.st_size,
                    'mtime': int(stat.st_mtime)
                })
            except OSError:
                continue
                
    return entries

def _build_tree_text(root: str, entries: list[dict]) -> str:
    paths = sorted([f['path'] for f in entries])
    tree = {}
    for path in paths:
        parts = path.split('/')
        current = tree
        for part in parts:
            if part not in current:
                current[part] = {}
            current = current[part]
            
    def render_tree(node, prefix="") -> list[str]:
        lines = []
        keys = sorted(node.keys())
        for i, key in enumerate(keys):
            is_last = (i == len(keys) - 1)
            connector = "└── " if is_last else "├── "
            
            if node[key]:  
                lines.append(f"{prefix}{connector}{key}/")
                new_prefix = prefix + ("    " if is_last else "│   ")
                lines.extend(render_tree(node[key], new_prefix))
            else:  
                lines.append(f"{prefix}{connector}{key}")
        return lines

    tree_lines = render_tree(tree)
    tree_str = "\n".join(tree_lines)
    return f"# Project Structure\n\nGenerated on: {datetime.now().isoformat()}\n\n```\n{tree_str}\n```\n"

def generate_or_update_structure(project_path: str) -> dict:
    entries = _scan_project(project_path)
    current_fp = _compute_fingerprint(entries)
    
    meta_path = os.path.join(project_path, "structure_meta.json")
    md_path = os.path.join(project_path, "PROJECT_STRUCTURE.md")
    
    old_fp = None
    if os.path.exists(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
                old_fp = meta.get("fingerprint")
        except Exception:
            pass

    structure_text = ""
    updated = False

    if current_fp != old_fp or not os.path.exists(md_path):
        structure_text = _build_tree_text(project_path, entries)
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(structure_text)
            
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump({
                "fingerprint": current_fp,
                "updated_at": datetime.now().isoformat(),
                "file_count": len(entries)
            }, f, indent=2)
        updated = True
    else:
        with open(md_path, "r", encoding="utf-8") as f:
            structure_text = f.read()

    return {
        "updated": updated,
        "fingerprint": current_fp,
        "file_count": len(entries),
        "structure_text": structure_text
    }

def get_structure_text(project_path: str) -> str:
    res = generate_or_update_structure(project_path)
    return res["structure_text"]