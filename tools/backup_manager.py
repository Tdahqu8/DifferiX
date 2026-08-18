import git
import os
from datetime import datetime
from pathlib import Path

# --- CONFIG ---
REPO_PATH = '.'
IGNORE_PATTERNS = [
    '.env',
    '*.log',
    'pycache/',
    '*.tmp',
]

def get_repo():
    """Repo initialisation..."""
    try:
        return git.Repo(REPO_PATH)
    except git.exc.InvalidGitRepositoryError:
        print("Error: current folder is not a git repo")
        print("Write 'git init' in terminal. Ensure that you located to that project's folder with cd .. and cd 'yourProjectName' ")
        exit(1)

def should_ignore(file_path):
    """Checking whether file is ignored"""
    file_name = os.path.basename(file_path)
    for pattern in IGNORE_PATTERNS:
        if pattern.startswith('*'):
            if file_name.endswith(pattern[1:]):
                return True
        elif pattern.endswith('/'):
            if pattern[:-1] in file_path:
                return True
        else:
            if file_name == pattern:
                return True
    return False

def make_auto_commit(repo, agent_name="system", comment=""):
    """Creating commits with file filtration and error processing:"""
    try:
        changed_files = [item.a_path for item in repo.index.diff(None)]
        untracked_files = repo.untracked_files
        all_candidates = changed_files + untracked_files

        files_to_add = [f for f in all_candidates if not should_ignore(f)]

        if not files_to_add:
            if not repo.is_dirty() and not untracked_files:
                print("No updates")
                return
            else:
                print("All updated files are ignored.")
                return

        repo.git.add(files_to_add)

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        message = f"Auto-save [{agent_name}]: {timestamp}"
        if comment:
            message += f" — {comment[:80]}"

        try:
            commit = repo.index.commit(message)
            print(f"Commit created: {message} (Хеш: {commit.hexsha[:7]})")
        except git.exc.GitCommandError as e:
            print(f"Commit error: {e}")
            return

    except Exception as e:
        print(f"Fatal error while auto-saving: {e}")

def safe_rollback(repo, target_hash):
    """
    Safe rollback: created a new branch identical to old one without deleting anything
    """
    branch_name = f"rollback-to-{target_hash[:7]}"
    try:
        commit = repo.commit(target_hash)
        
        new_branch = repo.create_head(branch_name, commit)
        
        repo.heads[branch_name].checkout()
        
        print(f"Rollbacked successfully: '{branch_name}'.")
        print(f"   Old files restored. Check it in 'main' or previous")
        print(f"   Write that command to return: git checkout main")
    except git.exc.GitError as e:
        print(f"Git error occurred: {e}")

def show_all_commits(repo, limit=None):
    """Show all tokens"""
    commits = list(repo.iter_commits())
    if limit is not None:
        commits = commits[:limit]

    print(f"\n--- Git commits ({len(commits)}) ---")
    for i, c in enumerate(commits):
        short = c.hexsha[:7]
        msg = c.message.strip().split('\n')[0][:70]
        date = c.committed_datetime.strftime("%Y-%m-%d %H:%M")
        print(f"{i:3}. {short} | {date} | {msg}")
    return commits


def create_success_tag(repo):
    """Создать тег только на успешном завершении"""
    try:
        tag_name = f"success-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        repo.create_tag(tag_name)
        print(f"Failed to create success tag: {tag_name}")
    except Exception as e:
        print(f"Cannot create tag: {e}")