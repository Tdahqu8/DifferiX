import os
from pathlib import Path

DIFFERIX_FRAMEWORK_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

DIFFERIX_FRAMEWORK_CORE_FILES = {
    ".env", "config.py", "state.json", "agents_chat.py",
    "agents_chat_2.py", "deduplicate.py", "PROJECT_STRUCTURE.md"
}

DIFFERIX_PROTECTED_DIRS = {"tools", "lessons", "iteration_logs"}

def is_differix_framework_file(target_file: str, project_dir: str = ".") -> bool:
    """
    Проверяет, принадлежит ли файл самому фреймворку DifferiX 
    (включая папки tools/, lessons/ и т.д.), защищая их от перезаписи агентами.
    """
    abs_target = os.path.abspath(os.path.join(project_dir, target_file))
    rel_to_root = os.path.relpath(abs_target, DIFFERIX_FRAMEWORK_ROOT)

    if rel_to_root.startswith(".."):
        return False

    parts = Path(rel_to_root).parts

    # 1. Защита файлов в корне фреймворка
    if len(parts) == 1 and parts[0] in DIFFERIX_FRAMEWORK_CORE_FILES:
        return True

    # 2. Защита служебных директорий (tools/*.py, lessons/*.json и др.)
    if len(parts) >= 2 and parts[0] in DIFFERIX_PROTECTED_DIRS:
        return True

    return False

def validate_sandboxed_path(target_file: str, project_dir: str = ".") -> str:
    """Проверяет границы песочницы и запрещает модификацию кода самого фреймворка."""
    base_path = os.path.abspath(project_dir)
    target_path = os.path.abspath(os.path.join(base_path, target_file))

    try:
        contained = os.path.commonpath([base_path, target_path]) == base_path
    except ValueError:
        contained = False

    if not contained:
        raise PermissionError(f"SANDBOX VIOLATION: Path '{target_file}' escapes boundary '{project_dir}'.")

    if is_differix_framework_file(target_file, project_dir):
        raise PermissionError(f"PROTECTION VIOLATION: Modification of framework system file '{target_file}' is forbidden.")

    return target_path
from tools.project_scanner import get_full_project_context

def get_project_context(project_dir: str = ".") -> str:
    return get_full_project_context(project_dir=project_dir)