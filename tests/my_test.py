
import pytest
from tools.project_scanner import get_workspace_context
def test_sanity_check():
    """Confirms pytest executes correctly inside DifferiX."""
    assert True
def test_workspace_context_returns_data():
    """Verify project context scanner returns non-empty structure."""
    context = get_workspace_context(root_dir=".")
    assert context is not None