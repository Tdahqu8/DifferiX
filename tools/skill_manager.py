import os
import json
from typing import Dict, Any, Optional

CONFIG_FILE = "skills_config.json"

# Framework-level skills directory (relative to the VegeX install root)
_FRAMEWORK_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_FRAMEWORK_SKILLS_DIR = os.path.join(_FRAMEWORK_ROOT, "skills")


class SkillManager:
    def __init__(self, config_path: str = CONFIG_FILE):
        self.config_path = config_path
        self._config: Optional[Dict[str, Any]] = None

    def _ensure_loaded(self):
        if self._config is not None:
            return
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    self._config = json.load(f)
            except Exception:
                self._config = self._get_default_config()
        else:
            self._config = self._get_default_config()

    def _get_default_config(self) -> Dict[str, Any]:
        return {
            "active_skills": {
                "researcher": "python",
                "editor": "python",
                "reviewer": "python",
            }
        }

    def save_config(self):
        if self._config is None:
            self._ensure_loaded()
        os.makedirs(os.path.dirname(os.path.abspath(self.config_path)), exist_ok=True)
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(self._config, f, indent=2, ensure_ascii=False)

    def get_skill_for_role(self, role: str) -> str:
        self._ensure_loaded()
        return self._config.get("active_skills", {}).get(role, "")

    def set_skill_for_role(self, role: str, skill_name: str):
        self._ensure_loaded()
        if "active_skills" not in self._config:
            self._config["active_skills"] = {}
        self._config["active_skills"][role] = skill_name
        self.save_config()


_default_manager: Optional[SkillManager] = None


def get_manager() -> SkillManager:
    global _default_manager
    if _default_manager is None:
        _default_manager = SkillManager()
    return _default_manager


def _read_skill_file(filename: str, search_dirs) -> str:
    """Read a skill file from the first directory where it exists."""
    for base in search_dirs:
        if not base:
            continue
        path = os.path.join(base, filename)
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return f.read().strip()
            except Exception:
                continue
    return ""


def get_combined_agent_skill(role: str, project_dir: str = ".") -> str:
    """
    Return the fully-assembled skill prompt for a role.

    Combines:
      1. The role-level rules file: `{role}_rules.md`  (e.g. `researcher_rules.md`)
      2. The domain skill configured in skills_config.json (e.g. `python.md`)

    Lookup order: project_dir/skills → framework_root/skills.
    Both directories are searched so per-project overrides work.
    """
    manager = get_manager()
    domain_skill_name = manager.get_skill_for_role(role)

    search_dirs = (
        os.path.join(project_dir, "skills") if project_dir else None,
        _FRAMEWORK_SKILLS_DIR,
    )

    role_file = f"{role}_rules.md"
    role_text = _read_skill_file(role_file, search_dirs)

    domain_text = ""
    if domain_skill_name:
        domain_text = _read_skill_file(f"{domain_skill_name}.md", search_dirs)

    parts = []
    if role_text:
        parts.append(f"=== ROLE RULES ({role}) ===\n{role_text}")
    if domain_text:
        parts.append(
            f"=== DOMAIN SKILL ({domain_skill_name}) ===\n{domain_text}"
        )

    if not parts:
        return (
            f"Standard skill definition for role: {role} "
            f"(no rules or domain skill file found; configured={domain_skill_name or 'None'})"
        )

    return "\n\n".join(parts)


__all__ = [
    "SkillManager",
    "get_manager",
    "get_combined_agent_skill",
]