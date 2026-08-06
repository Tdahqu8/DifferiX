import json
import os
import difflib
from datetime import datetime

LOGS_DIR = "./iteration_logs"

class ChangelogEngine:
    def __init__(self, session_id=None):
        os.makedirs(LOGS_DIR, exist_ok=True)
        if not session_id:
            session_id = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self.session_id = session_id
        self.log_filepath = os.path.join(LOGS_DIR, f"session_{session_id}.json")
        self.md_filepath = os.path.join(LOGS_DIR, f"session_{session_id}_SUMMARY.md")
        self._init_log_file()

    def _init_log_file(self):
        if not os.path.exists(self.log_filepath):
            initial_data = {
                "session_id": self.session_id,
                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "iterations": []
            }
            with open(self.log_filepath, "w", encoding="utf-8") as f:
                json.dump(initial_data, f, indent=2, ensure_ascii=False)

    def generate_diff(self, old_text: str, new_text: str, filename: str) -> dict:
        """Считает точный дифференциал изменений"""
        old_lines = old_text.splitlines(keepends=True)
        new_lines = new_text.splitlines(keepends=True)
        
        diff = list(difflib.unified_diff(old_lines, new_lines, fromfile=filename, tofile=filename))
        
        added = sum(1 for line in diff if line.startswith('+') and not line.startswith('+++'))
        removed = sum(1 for line in diff if line.startswith('-') and not line.startswith('---'))
        
        # Ограничиваем срез диффа для компактности
        clean_diff = "".join(diff[:40])
        
        return {
            "file": filename,
            "lines_added": added,
            "lines_removed": removed,
            "unified_diff": clean_diff if clean_diff else "Файл создан с нуля / Без изменений"
        }

    def record_iteration(self, state, diff_data=""):
        # 1. Гарантируем, что папка для логов существует
        log_dir = os.path.dirname(self.log_filepath)
        if log_dir and not os.path.exists(log_dir):
            os.makedirs(log_dir, exist_ok=True)

        # 2. Безопасно читаем существующую историю или создаем новый список
        history = []
        if os.path.exists(self.log_filepath):
            try:
                with open(self.log_filepath, "r", encoding="utf-8") as f:
                    history = json.load(f)
            except (json.JSONDecodeError, FileNotFoundError):
                history = []

        # 3. Формируем запись итеракции
        new_entry = {
            "iteration": state.get("iteration_count", 0),
            "step": state.get("current_step"),
            "target_file": state.get("generated_code", {}).get("target_file"),
            "review": state.get("review_result"),
            "diff": diff_data
        }
        history.append(new_entry)

        # 4. Перезаписываем файл
        with open(self.log_filepath, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)