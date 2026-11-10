import git
import os
from datetime import datetime
from pathlib import Path

REPO_PATH = '.'
IGNORE_PATTERNS = [
    '.env',
    '*.log',
    'pycache/',
    '*.tmp',
]

def get_repo(path_or_repo=None):
    """Repo initialisation with path safety check."""
    target = path_or_repo or REPO_PATH
    if isinstance(target, git.Repo):
        return target
    try:
        return git.Repo(target)
    except git.exc.InvalidGitRepositoryError:
        print(f"Error: path '{target}' is not a valid git repository.")
        print("Run 'git init' inside target project folder.")
        return None

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

def make_auto_commit(repo_or_path, agent_name="system", comment=""):
    """Creating commits with file filtration and error processing:"""
    repo = get_repo(repo_or_path)
    if not repo:
        return
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
            print(f"Commit created: {message} (SHA: {commit.hexsha[:7]})")
        except git.exc.GitCommandError as e:
            print(f"Commit error: {e}")
            return

    except Exception as e:
        print(f"Fatal error while auto-saving: {e}")

def safe_rollback(repo_or_path, target_hash):
    """Safe rollback to target commit."""
    repo = get_repo(repo_or_path)
    if not repo:
        return
    branch_name = f"rollback-to-{target_hash[:7]}"
    try:
        commit = repo.commit(target_hash)
        new_branch = repo.create_head(branch_name, commit)
        repo.heads[branch_name].checkout()
        print(f"Rollbacked successfully: '{branch_name}'.")
    except git.exc.GitError as e:
        print(f"Git error occurred: {e}")

def show_all_commits(repo_or_path, limit=15):
    """Display git commit list."""
    repo = get_repo(repo_or_path)
    if not repo:
        return []
    try:
        commits = list(repo.iter_commits())
        if limit is not None:
            commits = commits[:limit]

        print(f"\n--- Git Commits ({len(commits)}) ---")
        for i, c in enumerate(commits):
            short = c.hexsha[:7]
            msg = c.message.strip().split('\n')[0][:70]
            date = c.committed_datetime.strftime("%Y-%m-%d %H:%M")
            print(f"{i:3}. {short} | {date} | {msg}")
        return commits
    except Exception as e:
        print(f"Error reading commits: {e}")
        return []

def create_success_tag(repo_or_path):
    repo = get_repo(repo_or_path)
    if not repo:
        return
    try:
        tag_name = f"success-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        repo.create_tag(tag_name)
        print(f"Created success tag: {tag_name}")
    except Exception as e:
        print(f"Cannot create tag: {e}")