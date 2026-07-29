import json
import os
import sys
import config
from dotenv import load_dotenv

from tools.file_manager import FolderSandbox
from tools.compiler import run_universal_build
from tools.backup_manager import ProjectBackupSystem
from tools.changelog_engine import ChangelogEngine
from tools.project_scanner import get_project_files

from agents.researcher import run_researcher
from agents.editor import run_editor
from agents.reviewer import run_reviewer

load_dotenv()  # Подгружает переменные из .env файла

class OrchestratorConsole:
    def __init__(self):
        self.total_cost = 0.0

    def update_costs(self, meta):
        """Считает и выводит в консоль точные затраты на токены после каждого шага"""
        cost = (meta.prompt_token_count * (config.PRICE_INPUT_1M / 1_000_000)) + (meta.candidates_token_count * (config.PRICE_OUTPUT_1M / 1_000_000))
        self.total_cost += cost
        print(f"💰 [Токены за шаг]: Вход: {meta.prompt_token_count} | Выход: {meta.candidates_token_count} | Стоимость: ${cost:.5f}")
        print(f"💳 [Общий счет сессии]: ${self.total_cost:.5f}")

    def load_state(self):
        with open("state.json", "r", encoding="utf-8") as f: 
            return json.load(f)

    def save_state(self, state):
        with open("state.json", "w", encoding="utf-8") as f: 
            json.dump(state, f, indent=2, ensure_ascii=False)

    def start(self):
        print("====== 🤖 AI ORCHESTRATOR КОНСОЛЬ v1.0 ======")
        print("Выберите рабочий проект:")
        for k, v in config.PROJECTS.items():
            print(f"{k}) {v['name']} ({v['lang']})")
        
        choice = input("Введи номер проекта: ").strip()
        if choice not in config.PROJECTS:
            print("Выход.")
            return

        proj = config.PROJECTS[choice]
        sandbox = FolderSandbox(proj["path"])
        backup = ProjectBackupSystem(proj["path"])
        logger = ChangelogEngine() # Инициализируем систему летописи
        
        # Меню действий
        print("\nЧто сделать?")
        print("1) Запустить цикл ИИ-агентов для текущей задачи в state.json")
        print("2) Откатить проект к одному из ZIP-бэкапов")
        action = input("Введи номер действия: ").strip()

        if action == "2":
            backups = backup.list_backups()
            if not backups:
                print("Бэкапов для этого проекта не найдено.")
                return
            for idx, b in enumerate(backups): 
                print(f"{idx}) {b}")
            b_choice = int(input("Какой бэкап развернуть? Введи индекс: "))
            backup.restore_snapshot(backups[b_choice])
            return

        # Изначальный бэкап перед началом работы ИИ
        backup.create_snapshot("USER", "Before_AI_Work")

        # Переменная для хранения подсчитанной разницы файлов между шагами
        current_diff = None

        while True:
            state = self.load_state()
            # Сканируем структуру проекта на каждом круге
            project_files = get_project_files(proj["path"])

            # ПРЕДОХРАНИТЕЛЬ: Засыпание ИИ, чтобы не слить деньги
            if state["iteration_count"] >= config.MAX_ITERATIONS or self.total_cost >= config.MAX_BUDGET_USD:
                print("\n⚠️ АГЕНТЫ УШЛИ В ПАУЗУ И ЗАСНУЛИ, чтобы сберечь твои токены!")
                print(f"Последний статус билдера: {state['compiler_output']}")
                cmd = input("\nКоманда ([C]продолжить / [B]сделать бэкап и выйти / [R]стереть изменения): ").strip().lower()
                if cmd == 'c':
                    state["iteration_count"] = 0
                    self.save_state(state)
                    continue
                elif cmd == 'r':
                    backups = backup.list_backups()
                    if backups:
                        backup.restore_snapshot(backups[-1])
                    break
                else:
                    break

            # КРУГ АГЕНТОВ
            if state["current_step"] == "research":
                print("\n• [Шаг 1]: Исследователь строит план...")
                state, meta = run_researcher(state, proj["name"], project_files)
                self.update_costs(meta)
                self.save_state(state)

            elif state["current_step"] == "edit":
                print("\n• [Шаг 2]: Редактор пишет код...")
                state, meta = run_editor(state, project_files)
                self.update_costs(meta)
                
                res_file = state["generated_code"]["target_file"]
                res_code = state["generated_code"]["code"]

                # 1. Читаем старый файл перед записью для расчета Diff
                old_code = sandbox.safe_read_file(res_file)
                if old_code.startswith("ОШИБКА"):
                    old_code = "" # Если файла еще не было, считать его пустым

                # 2. Генерируем Diff
                current_diff = logger.generate_diff(old_code, res_code, res_file)

                # 3. Физическая безопасная запись
                print(sandbox.safe_write_code(res_file, res_code))
                
                self.save_state(state)

            elif state["current_step"] == "review":
                print("\n• [Шаг 3]: Запуск компиляции и оценка Ревизора...")
                success, log = run_universal_build(proj["path"], proj["lang"])
                state["compiler_output"] = log
                
                # Делаем автосохранение этой итерации
                backup.create_snapshot(f"Iter_{state['iteration_count']}", "AutoSave")

                if success:
                    print(f"🎉 СИНТАКСИС ИДЕАЛЕН! Код успешно собран. Логи:\n{log}")
                    state["current_step"] = "done"
                    self.save_state(state)
                    
                    # Фиксируем успех в летописи
                    logger.record_iteration(state, diff_data=current_diff)
                    
                    print("\n🏁 ИИ завершил работу. Время запускать и проводить ручной тест логики!")
                    break
                else:
                    # Сканируем ошибку Ревизором
                    state, meta = run_reviewer(state)
                    self.update_costs(meta)
                    self.save_state(state)

                    # Фиксируем итерацию с ошибкой в летописи
                    logger.record_iteration(state, diff_data=current_diff)

if __name__ == "__main__":
    OrchestratorConsole().start()