import os
import shutil
import zipfile
from datetime import datetime

BACKUP_STORE_DIR = "./AI_Orchestrator_Backups"

class ProjectBackupSystem:
    """Система автосохранений с точной датой, временем и именем корневой папки"""
    def __init__(self, target_project_path):
        self.project_path = os.path.abspath(target_project_path)
        self.project_name = os.path.basename(self.project_path)
        os.makedirs(BACKUP_STORE_DIR, exist_ok=True)

    def list_backups(self):
        """Возвращает список всех ZIP бэкапов для текущего проекта"""
        if not os.path.exists(BACKUP_STORE_DIR):
            return []
        files = [f for f in os.listdir(BACKUP_STORE_DIR) if f.startswith(self.project_name) and f.endswith('.zip')]
        return sorted(files)

    def create_snapshot(self, agent_name, comment=""):
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        archive_name = f"{self.project_name}_{timestamp}_{agent_name}"
        if comment:
            clean_comment = "".join(c for c in comment if c.isalnum() or c in ('_', '-'))[:20]
            archive_name += f"_{clean_comment}"
            
        zip_path = os.path.join(BACKUP_STORE_DIR, f"{archive_name}.zip")
        
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, dirs, files in os.walk(self.project_path):
                if any(ignore in root for ignore in [".git", "AI_Orchestrator_Backups", ".vs", "bin", "obj"]):
                    continue
                for file in files:
                    full_path = os.path.join(root, file)
                    rel_path = os.path.relpath(full_path, self.project_path)
                    zipf.write(full_path, rel_path)
        print(f"📦 Снимок сохранен: {zip_path}")
        return zip_path

    def restore_snapshot(self, zip_filename):
        zip_path = os.path.join(BACKUP_STORE_DIR, zip_filename)
        if not os.path.exists(zip_path):
            print("❌ Снимок не найден.")
            return False
            
        for item in os.listdir(self.project_path):
            item_path = os.path.join(self.project_path, item)
            if item in [".git", "AI_Orchestrator_Backups", ".vs", "bin", "obj"]:
                continue
            if os.path.isdir(item_path): 
                shutil.rmtree(item_path)
            else: 
                os.remove(item_path)
            
        with zipfile.ZipFile(zip_path, 'r') as zipf:
            zipf.extractall(self.project_path)
        print(f"🔄 Проект '{self.project_name}' успешно откачен к {zip_filename}!")
        return True