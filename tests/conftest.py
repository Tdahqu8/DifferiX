"""
Test-local conftest. Fixtures here are available to all tests in tests/.

Everything is built on tmp_path so nothing escapes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.state_manager import AppState, build_state_template
from memory import SQLiteMemoryStorage, MemoryManager


@pytest.fixture
def empty_state() -> AppState:
    """A clean AppState with no task."""
    return build_state_template()


@pytest.fixture
def active_state() -> AppState:
    """An AppState with a task prompt already set."""
    return build_state_template(user_prompt="test task")


@pytest.fixture
def tmp_storage(tmp_path: Path) -> SQLiteMemoryStorage:
    """An in-sandbox SQLite storage at a temp path."""
    return SQLiteMemoryStorage(db_path=tmp_path / "test_memory.db")


@pytest.fixture
def tmp_manager(tmp_path: Path) -> MemoryManager:
    """A MemoryManager backed by an isolated database."""
    storage = SQLiteMemoryStorage(db_path=tmp_path / "test_memory.db")
    return MemoryManager(storage=storage)


@pytest.fixture
def sample_lesson_file(tmp_path: Path) -> Path:
    """Writes one valid lesson JSON file to tmp and returns its path."""
    lessons_dir = tmp_path / "lessons" / "promoted"
    lessons_dir.mkdir(parents=True)
    lesson_file = lessons_dir / "test_lesson.json"
    lesson_file.write_text(
        '{"id": "test", "root_cause": "test cause", "details": {}}',
        encoding="utf-8",
    )
    return lesson_file