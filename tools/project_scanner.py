# tools/project_scanner.py
import os

def get_project_files(root_dir):
    ignore = {'.git', '.vs', 'bin', 'obj', 'AI_Orchestrator_Backups', 'iteration_logs'}
    files_list = []
    for root, dirs, files in os.walk(root_dir):
        dirs[:] = [d for d in dirs if d not in ignore]
        for f in files:
            if f.endswith(('.cs', '.xml')):
                rel = os.path.relpath(os.path.join(root, f), root_dir)
                files_list.append(rel.replace("\\", "/"))
    return "\n".join(files_list)