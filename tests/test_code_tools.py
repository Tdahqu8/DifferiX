import pytest
from pathlib import Path
from tools.code_tools import (
    run_syntax_check,
    run_snippet,
    check_imports,
    add_dependency
)

pytestmark = pytest.mark.unit

def test_run_syntax_check_on_valid_file(tmp_path: Path):
    """Test syntax check passes on valid Python file."""
    test_file = tmp_path / "valid.py"
    test_file.write_text("def foo(): pass", encoding="utf-8")
    
    result = run_syntax_check(test_file)
    assert result["ok"] is True

def test_run_syntax_check_on_broken_file(tmp_path: Path):
    """Test syntax check fails on invalid Python file."""
    test_file = tmp_path / "broken.py"
    test_file.write_text("def foo(:", encoding="utf-8")
    
    result = run_syntax_check(test_file)
    assert result["ok"] is False
    assert "SyntaxError" in result["error"]

def test_run_snippet_captures_output():
    """Test snippet execution captures stdout."""
    result = run_snippet("print('hello')")
    assert result["ok"] is True
    assert "hello" in result["stdout"]

def test_run_snippet_timeout():
    """Test snippet execution times out."""
    result = run_snippet("while True: pass", timeout=1)
    assert result["ok"] is False
    assert result["error"] == "timeout"

def test_check_imports_missing(tmp_path: Path):
    """Test missing imports are detected."""
    test_file = tmp_path / "imports.py"
    test_file.write_text("import nonexistent_module_xyz_123", encoding="utf-8")
    
    result = check_imports(test_file)
    assert "nonexistent_module_xyz_123" in result["missing"]

def test_add_dependency_rejects_bad_name():
    """Test invalid package names are rejected."""
    result = add_dependency("bad_name; rm -rf /")
    assert result["ok"] is False
    assert "invalid" in result["error"].lower()