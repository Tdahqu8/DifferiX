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

    def record_iteration(self, state: dict, diff_data: dict = None):
        """Записывает шаг в JSON и сразу же обновляет Markdown-сводку"""
        with open(self.log_filepath, "r", encoding="utf-8") as f:
            log_data = json.load(f)

        entry = {
            "iteration": state.get("iteration_count", 0),
            "timestamp": datetime.now().strftime("%H:%M:%S"),
            "step_status": state.get("current_step"),
            "target_file": state.get("generated_code", {}).get("target_file", "Не указан"),
            "diff": diff_data if diff_data else {},
            "compiler_success": state.get("current_step") == "done",
            "compiler_log": state.get("compiler_output", "")[:400],
            "research_plan": state.get("research_data", "")[:300]
        }

        log_data["iterations"].append(entry)

        with open(self.log_filepath, "w", encoding="utf-8") as f:
            json.dump(log_data, f, indent=2, ensure_ascii=False)

        # Автоматически обновляем Markdown файл после каждой итерации
        self._render_markdown_summary(log_data)
        print(f"📜 Летопись и Markdown-сводка обновлены!")

    def _render_markdown_summary(self, log_data: dict):
        """Генерирует готовый красивый MD-пост для чтения и коммьюнити"""
        md = []
        md.append(f"# 📜 Отчет сессии f(x): `{log_data['session_id']}`")
        md.append(f"**Дата старта:** {log_data['created_at']}")
        md.append("---")

        for it in log_data["iterations"]:
            status_icon = "🎉 УСПЕХ" if it["compiler_success"] else "❌ ОШИБКА СБОРКИ"
            diff_info = it.get("diff", {})
            added = diff_info.get("lines_added", 0)
            removed = diff_info.get("lines_removed", 0)

            md.append(f"## 🔄 Итерация #{it['iteration']} `[{it['timestamp']}]` — Статус: {status_icon}")
            md.append(f"* **Измененный файл:** `{it['target_file']}` (`+{added}` / `-{removed}` строк)")
            
            if it.get("research_plan"):
                md.append(f"\n> **План Исследователя:**\n> {it['research_plan']}...")

            if diff_info.get("unified_diff"):
                md.append("\n<details><summary>🔍 Нажмите, чтобы посмотреть Diff кода</summary>\n")
                md.append("```diff")
                md.append(diff_info["unified_diff"])
                md.append("```\n</details>\n")

            if not it["compiler_success"] and it.get("compiler_log"):
                md.append("\n🚨 **Лог ошибки компилятора:**")
                md.append("```text")
                md.append(it["compiler_log"])
                md.append("```")

            md.append("\n---")

        with open(self.md_filepath, "w", encoding="utf-8") as f:
            f.write("\n".join(md))