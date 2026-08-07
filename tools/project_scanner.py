import os
import subprocess

def get_workspace_context(root_dir: str = ".") -> str:
    """Provides explicit environment state to prevent path hallucinations."""
    ignore_dirs = {".git", "__pycache__", "venv", ".venv", ".pytest_cache"}
    
    # 1. Directory Tree
    tree_lines = ["--- WORKSPACE TREE ---"]
    for root, dirs, files in os.walk(root_dir):
        dirs[:] = [d for d in dirs if d not in ignore_dirs]
        level = root.replace(root_dir, '').count(os.sep)
        indent = ' ' * 4 * level
        tree_lines.append(f"{indent}{os.path.basename(root)}/")
        subindent = ' ' * 4 * (level + 1)
        for f in files:
            tree_lines.append(f"{subindent}{f}")
            
    # 2. Git Working Directory Verification
    git_status = "Not a Git Repo"
    try:
        res = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], 
                            cwd=root_dir, capture_output=True, text=True)
        if res.returncode == 0:
            git_status = f"Valid Git Repo (Root: {os.path.abspath(root_dir)})"
    except Exception as e:
        git_status = f"Git Check Failed: {str(e)}"

    tree_lines.append(f"\n--- ENVIRONMENT INFO ---")
    tree_lines.append(f"Current Working Dir: {os.path.abspath(root_dir)}")
    tree_lines.append(f"Git Repository Status: {git_status}")
    
    return "\n".join(tree_lines)