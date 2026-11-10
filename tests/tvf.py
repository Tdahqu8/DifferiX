import pytest
from tools.security.verification_firewall import (
    fs_hash, verify_file_changed, verify_build_result,
)

@pytest.mark.unit
def test_fs_hash_detects_change(tmp_path):
    p = tmp_path / "test.py"
    p.write_text("original")
    h1 = fs_hash(str(p))
    p.write_text("changed")
    h2 = fs_hash(str(p))
    assert h1 != h2

@pytest.mark.unit
def test_verify_file_changed_detects_no_change(tmp_path):
    p = tmp_path / "test.py"
    p.write_text("same")
    h = fs_hash(str(p))
    changed, _ = verify_file_changed(str(p), h)
    assert not changed

@pytest.mark.unit
def test_verify_build_result_agent_lies():
    result = verify_build_result(
        claimed_success=True,
        actual_success=False,
        actual_log="BUILD_FAILED",
    )
    assert not result["trusted_success"]
    assert not result["consistent"]
    assert any("simulated test passed" in w for w in result["warnings"])