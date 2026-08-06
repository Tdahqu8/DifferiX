import git
import os
from datetime import datetime
from pathlib import Path

# --- КОНФИГУРАЦИЯ ---
REPO_PATH = '.'  # Путь к репозиторию (текущая папка)
IGNORE_PATTERNS = [
    '.env',           # Файлы с ключами
    '*.log',          # Лог-файлы
    'pycache/',   # Кэш Python
    '*.tmp',          # Временные файлы
]

def get_repo():
    """Инициализация репозитория"""
    try:
        return git.Repo(REPO_PATH)
    except git.exc.InvalidGitRepositoryError:
        print("❌ Ошибка: Текущая папка не является Git-репозиторием.")
        print("💡 Решение: Выполните 'git init' в терминале.")
        exit(1)

def should_ignore(file_path):
    """Проверка: попадает ли файл в список игнорируемых"""
    file_name = os.path.basename(file_path)
    for pattern in IGNORE_PATTERNS:
        # Простая проверка по имени или расширению
        if pattern.startswith('*'):
            if file_name.endswith(pattern[1:]): # Например *.log
                return True
        elif pattern.endswith('/'):
            if pattern[:-1] in file_path: # Например pycache/
                return True
        else:
            if file_name == pattern: # Точное совпадение, например .env
                return True
    return False

def make_auto_commit(repo):
    """Создание коммита с обработкой ошибок и фильтрацией файлов"""
    try:
        # 1. Получаем список измененных файлов
        changed_files = [item.a_path for item in repo.index.diff(None)]
        untracked_files = repo.untracked_files
        all_candidates = changed_files + untracked_files

        # 2. Фильтруем файлы (исключаем .env и другие из списка)
        files_to_add = [f for f in all_candidates if not should_ignore(f)]

        if not files_to_add:
            # Если после фильтрации файлов нет, проверяем, были ли вообще изменения
            if not repo.is_dirty() and not untracked_files:
                print("ℹ️ Изменений нет.")
                return
            else:
                print("⚠️ Изменения есть, но все файлы попали в список игнорируемых (.env и др.). Пропуск.")
                return

        # 3. Добавляем только разрешенные файлы
        # Примечание: git.add() принимает пути. Для точности добавляем по одному или списком
        repo.git.add(files_to_add)

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        message = f"Auto-save: {timestamp}"

        # 4. Попытка коммита
        try:
            commit = repo.index.commit(message)
            print(f"✅ Коммит создан: {message} (Хеш: {commit.hexsha[:7]})")
        except git.exc.GitCommandError as e:
            print(f"❌ Ошибка при коммите: {e}")
            return

        # 5. Попытка создания тега
        try:
            tag_name = f"save-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
            repo.create_tag(tag_name)
            print(f"🏷️ Тег создан: {tag_name}")
        except git.exc.GitCommandError as e:
            print(f"⚠️ Ошибка при создании тега (возможно, он уже есть): {e}")
            # Мы не прерываем выполнение, так как коммит уже успешен

    except Exception as e:
        print(f"❌ Критическая ошибка в процессе автосохранения: {e}")

def safe_rollback(repo, target_hash):
    """
    Безопасный откат: создает новую ветку с состоянием прошлого коммита.
    Это не удаляет историю, а создает точку восстановления.
    """
    branch_name = f"rollback-to-{target_hash[:7]}"
    try:
        # Проверяем, существует ли такой хеш
        commit = repo.commit(target_hash)
        
        # Создаем новую ветку, указывающую на старый коммит
        new_branch = repo.create_head(branch_name, commit)
        
        # Переключаемся на эту ветку
        repo.heads[branch_name].checkout()
        
        print(f"🔙 Успешный откат! Создана и активирована ветка '{branch_name}'.")
        print(f"   Старые файлы восстановлены. История сохранена в ветке 'main' (или предыдущей).")
        print(f"   Чтобы вернуться назад, просто переключитесь на старую ветку: git checkout main")
    except git.exc.GitError as e:
        print(f"Git error occurred: {e}")