import json
import pytest

from tools.reflection import (
    should_reflect,
    _normalize_reason,
    _extract_failures,
    _cluster_failures,
    _load_recent_logs,
)


@pytest.mark.unit
def test_should_reflect_interval():
    assert should_reflect(0) is False
    assert should_reflect(5) is True
    assert should_reflect(10) is True
    assert should_reflect(7) is False


@pytest.mark.unit
def test_normalize_reason_collapses_variants():
    a = _normalize_reason("Coherence report contains blocking errors: missing file references")
    b = _normalize_reason("Coherence report has blocking errors: missing file references.")
    assert a == b


@pytest.mark.unit
def test_cluster_requires_min_occurrences():
    failures = [
        {"cycle": "1", "reason": "coherence error A", "target_file": "a.css", "kind": "x"},
        {"cycle": "2", "reason": "coherence error A", "target_file": "a.css", "kind": "x"},
        {"cycle": "3", "reason": "unique one-off failure", "target_file": "b.py", "kind": "x"},
    ]
    clusters = _cluster_failures(failures)
    assert len(clusters) == 1
    assert clusters[0]["count"] == 2
    assert clusters[0]["sample_targets"] == ["a.css"]


@pytest.mark.unit
def test_extract_failures_from_rejected_logs(tmp_path):
    log = {
        "iteration_count": 3,
        "review_result": {"verdict": "REJECT", "reason": "test reason"},
        "active_target_file": "styles.css",
    }
    (tmp_path / "iteration_logs").mkdir()
    (tmp_path / "iteration_logs" / "s1.json").write_text(json.dumps(log))

    logs = _load_recent_logs(str(tmp_path))
    failures = _extract_failures(logs)
    assert len(failures) == 1
    assert failures[0]["reason"] == "test reason"
    assert failures[0]["target_file"] == "styles.css"


@pytest.mark.unit
def test_extract_failures_from_compiler_reject(tmp_path):
    log = {
        "iteration_count": 5,
        "compiler_output": "REVIEWER_REJECTED: coherence failed\nBuild output: ...",
        "active_target_file": "index.html",
    }
    (tmp_path / "iteration_logs").mkdir()
    (tmp_path / "iteration_logs" / "s2.json").write_text(json.dumps(log))

    logs = _load_recent_logs(str(tmp_path))
    failures = _extract_failures(logs)
    assert len(failures) == 1
    assert "coherence failed" in failures[0]["reason"]


@pytest.mark.unit
def test_normalize_reason_truncates_long_text():
    long_reason = " ".join(["word"] * 50)
    assert len(_normalize_reason(long_reason).split()) == 10