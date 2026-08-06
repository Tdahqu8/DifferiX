import json
import os
import sys
from datetime import datetime

import config
from dotenv import load_dotenv

from tools.file_manager import FolderSandbox
from tools.compiler import run_universal_build
from tools.backup_manager import (
    get_repo,
    make_auto_commit,
    safe_rollback,
    show_all_commits,
    create_success_tag,
)
from tools.changelog_engine import ChangelogEngine
from tools.project_scanner import get_project_files

from agents.researcher import run_researcher
from agents.editor import run_editor
from agents.reviewer import run_reviewer

load_dotenv()


class OrchestratorConsole:
    def __init__(self):
        self.total_cost = 0.0
        self.should_pause = False
        self.should_stop = False
        self.proj = None
        self.sandbox = None
        self.logger = None

    def update_costs(self, meta):
        """Считает и выводит в консоль точные затраты на токены после каждого шага"""
        cost = (meta.prompt_token_count * (config.PRICE_INPUT_1M / 1_000_000)) + (
            meta.candidates_token_count * (config.PRICE_OUTPUT_1M / 1_000_000)
        )
        self.total_cost += cost
        print(
            f"💰 [Токены за шаг]: Вход: {meta.prompt_token_count} | "
            f"Выход: {meta.candidates_token_count} | Стоимость: ${cost:.5f}"
        )
        print(f"💳 [Общий счет сессии]: ${self.total_cost:.5f}")

    def load_state(self):
        with open("state.json", "r", encoding="utf-8") as f:
            return json.load(f)

    def save_state(self, state):
        with open("state.json", "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)

    def print_help(self):
        print(
            """
Доступные команды:
  run / start         — запустить цикл агентов
  pause               — прервать цикл после текущего шага + показать changelog
  stop                — полностью остановить цикл
  status              — текущий статус
  log / changelog     — показать летопись текущей сессии
  warn low|mid|high   — выдать предупреждение агентам
  rollback            — откат к выбранному коммиту
  history / commits   — список ВСЕХ git-коммитов
  help                — эта справка
  exit / quit         — выход
"""
        )

    def show_status(self):
        try:
            state = self.load_state()
            print("\n--- STATUS ---")
            print(f"Текущий шаг:     {state.get('current_step')}")
            print(f"Итерация:        {state.get('iteration_count')}")
            print(f"Цель:            {str(state.get('goal', '—'))[:80]}")
            print(f"Последний билд:  {str(state.get('compiler_output', '—'))[:120]}")
            print(f"need_human:      {state.get('need_human', False)}")
            print(f"Предупреждений:  {len(state.get('warnings', []))}")
            print("---------------")
        except Exception as e:
            print(f"Не удалось прочитать state: {e}")

    def show_changelog(self):
        log_dir = "./iteration_logs"
        if not os.path.exists(log_dir):
            print("Папка iteration_logs пуста.")
            return

        files = sorted(
            [f for f in os.listdir(log_dir) if f.endswith("_SUMMARY.md")],
            reverse=True,
        )
        if not files:
            print("Summary-файлов пока нет.")
            return

        latest = files[0]
        path = os.path.join(log_dir, latest)
        print(f"\n--- Changelog: {latest} ---")
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
            print(content[:4000])
            if len(content) > 4000:
                print("\n... (обрезано)")
        print("--- конец ---")

    def issue_warning(self, level: str, text: str = ""):
        levels = {
            "low": "DIRECTIVE",
            "mid": "WARNING",
            "high": "HARD_STOP",
        }
        tag = levels.get(level)
        if not tag:
            print("Используй: warn low | warn mid | warn high")
            return

        warning = {
            "level": tag,
            "text": text or f"Manual {tag} from operator",
            "timestamp": datetime.now().isoformat(),
        }

        try:
            state = self.load_state()
        except Exception:
            state = {}

        state.setdefault("warnings", []).append(warning)
        if level == "high":
            state["need_human"] = True
            self.should_pause = True
        self.save_state(state)

        os.makedirs("status", exist_ok=True)
        with open("status/last_warning.md", "w", encoding="utf-8") as f:
            f.write(f"# {tag}\n\n{warning['text']}\n\n{warning['timestamp']}\n")

        print(f"⚠️ Выдано предупреждение уровня {tag}")

    def run_agent_cycle(self):
        """Основной цикл агентов"""
        if not self.proj or not self.sandbox or not self.logger:
            print("Проект не выбран.")
            return

        print("\n🚀 Запуск цикла агентов...")
        current_diff = None
        self.should_pause = False
        self.should_stop = False

        while True:
            # Проверки паузы / остановки между шагами
            if self.should_stop:
                print("⏹ Цикл остановлен по команде.")
                self.should_stop = False
                break

            if self.should_pause:
                print("⏸ Цикл на паузе. Показываю changelog:")
                self.show_changelog()
                self.should_pause = False
                break

            state = self.load_state()
            project_files = get_project_files(self.proj["path"])

            # Предохранитель по итерациям и бюджету
            if (
                state.get("iteration_count", 0) >= config.MAX_ITERATIONS
                or self.total_cost >= config.MAX_BUDGET_USD
            ):
                print(
                    "\n⚠️ АГЕНТЫ УШЛИ В ПАУЗУ, чтобы сберечь токены!"
                )
                print(f"Последний статус билдера: {state.get('compiler_output')}")
                cmd = (
                    input(
                        "\nКоманда ([C]продолжить / [S]стоп / [Enter]просто выйти в меню): "
                    )
                    .strip()
                    .lower()
                )
                if cmd == "c":
                    state["iteration_count"] = 0
                    self.save_state(state)
                    continue
                else:
                    break

            # --- КРУГ АГЕНТОВ ---
            if state.get("current_step") == "research":
                print("\n• [Шаг 1]: Исследователь строит план...")
                state, meta = run_researcher(state, self.proj["name"], project_files)
                self.update_costs(meta)
                self.save_state(state)

            elif state.get("current_step") == "edit":
                print("\n• [Шаг 2]: Редактор пишет код...")
                state, meta = run_editor(state, project_files)
                self.update_costs(meta)

                res_file = state["generated_code"]["target_file"]
                res_code = state["generated_code"]["code"]

                # Читаем старый файл для diff
                old_code = self.sandbox.safe_read_file(res_file)
                if old_code.startswith("ОШИБКА"):
                    old_code = ""

                current_diff = self.logger.generate_diff(old_code, res_code, res_file)

                # Безопасная запись
                print(self.sandbox.safe_write_code(res_file, res_code))
                self.save_state(state)

            elif state.get("current_step") == "review":
                print("\n• [Шаг 3]: Запуск компиляции и оценка Ревизора...")
                success, log = run_universal_build(self.proj["path"], self.proj["lang"])
                state["compiler_output"] = log

                if success:
                    print(f"🎉 СИНТАКСИС ИДЕАЛЕН! Код успешно собран.\n{log}")
                    state["current_step"] = "done"
                    self.save_state(state)

                    # Коммит только при успехе
                    try:
                        repo = get_repo()
                        make_auto_commit(
                            repo, "orchestrator", "Success after debugging"
                        )
                        create_success_tag(repo)
                    except Exception as e:
                        print(f"Не удалось сделать success-коммит: {e}")

                    self.logger.record_iteration(state, diff_data=current_diff)
                    print("\n🏁 ИИ завершил работу. Можно тестировать логику вручную.")
                    break
                else:
                    # Ревизор разбирает ошибку
                    state, meta = run_reviewer(state)
                    self.update_costs(meta)
                    self.save_state(state)
                    self.logger.record_iteration(state, diff_data=current_diff)

            else:
                print(
                    f"Неизвестный или завершённый шаг: {state.get('current_step')}. Выход из цикла."
                )
                break

    def start(self):
        print("====== 🤖 f(x) Orchestrator ======")
        print("Введи help для списка команд.\n")

        # Выбор проекта один раз
        print("Выберите рабочий проект:")
        for k, v in config.PROJECTS.items():
            print(f"{k}) {v['name']} ({v['lang']})")

        choice = input("Введи номер проекта: ").strip()
        if choice not in config.PROJECTS:
            print("Выход.")
            return

        self.proj = config.PROJECTS[choice]
        self.sandbox = FolderSandbox(self.proj["path"])
        self.logger = ChangelogEngine()

        # Один коммит перед началом работы
        try:
            repo = get_repo()
            make_auto_commit(repo, "USER", "Before AI work")
        except Exception as e:
            print(f"Не удалось сделать начальный коммит: {e}")

        # Главный цикл команд
        while True:
            cmd_line = input("f(x)> ").strip()
            if not cmd_line:
                continue

            parts = cmd_line.split(maxsplit=1)
            head = parts[0].lower()
            arg = parts[1] if len(parts) > 1 else ""

            if head in ("exit", "quit"):
                print("Выход.")
                break

            elif head == "help":
                self.print_help()

            elif head in ("run", "start"):
                self.run_agent_cycle()

            elif head == "pause":
                self.should_pause = True
                print("⏸ Пауза будет выполнена после текущего шага.")

            elif head == "stop":
                self.should_stop = True
                print("⏹ Остановка запрошена.")

            elif head == "status":
                self.show_status()

            elif head in ("log", "changelog"):
                self.show_changelog()

            elif head == "warn":
                level = arg.strip().lower()
                self.issue_warning(level)

            elif head in ("history", "commits"):
                try:
                    repo = get_repo()
                    show_all_commits(repo)
                except Exception as e:
                    print(f"Ошибка: {e}")

            elif head == "rollback":
                try:
                    repo = get_repo()
                    commits = show_all_commits(repo)
                    if not commits:
                        print("Коммитов нет.")
                        continue
                    raw = input("Номер коммита или короткий хеш: ").strip()
                    if raw.isdigit():
                        target = commits[int(raw)].hexsha
                    else:
                        target = raw
                    safe_rollback(repo, target)
                except Exception as e:
                    print(f"Ошибка отката: {e}")

            else:
                print("Неизвестная команда. Введи help.")


if __name__ == "__main__":
    OrchestratorConsole().start()