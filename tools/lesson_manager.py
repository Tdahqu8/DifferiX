import os
import json
import uuid
from datetime import datetime
from typing import List, Optional, Dict, Any

class LessonManager:
    """
    Manages the lifecycle of lessons using a folder-based structure.
    Statuses: observation, candidate, promoted, deprecated
    """
    STATUS_MAP = {
        "observations": "observation",
        "candidates": "candidate",
        "promoted": "promoted",
        "deprecated": "deprecated"
    }

    def __init__(self, base_dir: str = "lessons"):
        self.base_dir = base_dir
        self.statuses = list(self.STATUS_MAP.keys())
        self._init_dirs()

    def _init_dirs(self):
        for status in self.statuses:
            os.makedirs(os.path.join(self.base_dir, status), exist_ok=True)

    def create_observation(
        self, 
        rule: str, 
        why: str, 
        tags: List[str], 
        source: Optional[str] = None, 
        related_files: Optional[List[str]] = None, 
        priority: Optional[int] = None
    ) -> str:
        """Creates a new lesson in the observations folder."""
        timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        short_hash = uuid.uuid4().hex[:6]
        lesson_id = f"L-{timestamp}-{short_hash}"
        
        lesson_data = {
            "id": lesson_id,
            "status": "observation",
            "rule": rule,
            "why": why,
            "tags": tags,
            "created_at": datetime.now().isoformat(),
            "source": source,
            "related_files": related_files or [],
            "priority": priority or 1
        }
        
        file_path = os.path.join(self.base_dir, "observations", f"{lesson_id}.json")
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(lesson_data, f, indent=4, ensure_ascii=False)
            
        return lesson_id

    def load_lesson(self, lesson_id: str) -> Optional[Dict[str, Any]]:
        """Searches all status folders for a specific lesson ID."""
        for status in self.statuses:
            file_path = os.path.join(self.base_dir, status, f"{lesson_id}.json")
            if os.path.exists(file_path):
                with open(file_path, "r", encoding="utf-8") as f:
                    return json.load(f)
        return None

    def list_lessons(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        """Lists all lessons, optionally filtered by status folder name."""
        target_statuses = [status] if status in self.statuses else self.statuses
        lessons = []
        
        for st in target_statuses:
            folder_path = os.path.join(self.base_dir, st)
            if not os.path.exists(folder_path):
                continue
            for fname in os.listdir(folder_path):
                if fname.endswith(".json"):
                    with open(os.path.join(folder_path, fname), "r", encoding="utf-8") as f:
                        lessons.append(json.load(f))
        return lessons

    def move_lesson(self, lesson_id: str, new_status: str) -> bool:
        """Moves a lesson to a new status folder and updates its internal status field."""
        if new_status not in self.STATUS_MAP:
            raise ValueError(f"Invalid status: {new_status}. Must be one of {self.statuses}")

        lesson = self.load_lesson(lesson_id)
        if not lesson:
            return False

        # Locate current file
        current_file = None
        for status in self.statuses:
            p = os.path.join(self.base_dir, status, f"{lesson_id}.json")
            if os.path.exists(p):
                current_file = p
                break

        if not current_file:
            return False

        new_path = os.path.join(self.base_dir, new_status, f"{lesson_id}.json")
        
        # Update internal status field
        lesson["status"] = self.STATUS_MAP[new_status]

        # Save to new location
        with open(new_path, "w", encoding="utf-8") as f:
            json.dump(lesson, f, indent=4, ensure_ascii=False)

        # Remove old file only if the folder actually changed
        if os.path.abspath(current_file) != os.path.abspath(new_path):
            os.remove(current_file)

        return True