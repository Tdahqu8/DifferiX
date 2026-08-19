import os
import re

def extract_plan_dependencies(plan_data):
    """
    Extracts referenced file paths (e.g. 'tools/lesson_manager.py', 'config.py') 
    from the researcher's analysis or plan dict/list.
    """
    if not plan_data:
        return []
        
    text_to_search = str(plan_data)
    # Match strings looking like relative paths with common code extensions
    path_pattern = r'[\w\-/]+\.(?:py|json|md|cs|xml|txt)'
    matches = re.findall(path_pattern, text_to_search)
    
    return list(set(matches))
def get_project_structure(root_dir, allowed_exts=('.py', '.json', '.md', '.cs', '.xml')):
    """Scans and lists relative file paths in the project."""
    ignore = {'.git', '.vs', 'bin', 'obj', 'AI_Orchestrator_Backups', 'iteration_logs', 'venv', '__pycache__'}
    files_list = []
    for root, dirs, files in os.walk(root_dir):
        dirs[:] = [d for d in dirs if d not in ignore]
        for f in files:
            if f.endswith(allowed_exts):
                rel = os.path.relpath(os.path.join(root, f), root_dir)
                files_list.append(rel.replace("\\", "/"))
    return files_list

def read_file_content(root_dir, rel_path, max_kb=100):
    """Safely reads a single file from the project root."""
    full_path = os.path.join(root_dir, rel_path)
    if not os.path.exists(full_path):
        return f"--- FILE: {rel_path} (File not found) ---"
    
    # Check file size to avoid blowing up context window
    if os.path.getsize(full_path) > max_kb * 1024:
        return f"--- FILE: {rel_path} (File exceeds {max_kb}KB size limit) ---"
        
    try:
        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        return f"--- FILE: {rel_path} ---\n{content}\n"
    except Exception as e:
        return f"--- FILE: {rel_path} (Error reading file: {e}) ---"

def get_full_project_context(root_dir, target_files=None, core_files=("config.py", "tools/lesson_manager.py")):
    """
    Returns directory tree AND complete source code for core modules + task dependencies.
    """
    all_files = get_project_structure(root_dir)
    
    # Build Project Tree View
    tree_str = "### PROJECT STRUCTURE ###\n" + "\n".join(all_files) + "\n\n"
    
    # Determine which files need full content injection
    files_to_read = set(core_files)
    if target_files:
        files_to_read.update(target_files)
        
    # Read actual code contents
    contents_str = "### FILE CONTENTS CONTEXT ###\n\n"
    for rel_path in files_to_read:
        # Normalize relative path separators
        clean_rel = rel_path.replace("\\", "/").strip("/")
        if clean_rel in all_files or os.path.exists(os.path.join(root_dir, clean_rel)):
            contents_str += read_file_content(root_dir, clean_rel) + "\n"
            
    return tree_str + contents_str