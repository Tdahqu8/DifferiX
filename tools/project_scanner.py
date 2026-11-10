import os
import re
from typing import List, Tuple

def extract_plan_dependencies(plan_text: str) -> List[str]:
    """
    Извлекает пути к файлам, упомянутым в тексте исследования/плана.
    Поддерживает пути в форматах Linux/macOS (/) и Windows (\\).
    """
    pattern = r'[\w\-\.\/\\]+\.(?:py|json|md|cs|xml|txt)'
    matches = re.findall(pattern, plan_text)
    # Нормализуем разделители и убираем дубликаты
    normalized = [m.replace('\\', '/') for m in matches]
    return list(dict.fromkeys(normalized))

def read_file_content(filepath: str, max_lines: int = 150) -> str:
    """Безопасно читает содержимое файла с ограничением по количеству строк."""
    if not os.path.exists(filepath):
        return f"--- FILE: {filepath} (File not found) ---"
    try:
        with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.readlines()
            if len(lines) > max_lines:
                content = "".join(lines[:max_lines]) + f"\n... [TRUNCATED: Total {len(lines)} lines]"
            else:
                content = "".join(lines)
        return f"--- FILE: {filepath} ---\n{content}"
    except Exception as e:
        return f"--- FILE: {filepath} (Error reading: {e}) ---"

def get_full_project_context(
    project_dir: str = ".",
    core_files: Tuple[str, ...] = ("config.py", "tools/state_manager.py"),
    max_lines_per_file: int = 150
) -> str:
    """Формирует контекст проекта на основе реально существующих базовых модулей."""
    context_parts = []
    for file_path in core_files:
        full_path = os.path.join(project_dir, file_path)
        context_parts.append(read_file_content(full_path, max_lines=max_lines_per_file))
    return "\n\n".join(context_parts)