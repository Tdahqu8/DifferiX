"""
tools/security/verification_firewall.py
----------------------------------------
Agent self-reports are ADVISORY. Only external signals are AUTHORITATIVE.

Problem this solves (August 2026 incident):
  An agent in a loop hallucinated "test passed" and the pipeline believed it,
  because `state.get("compiler_output")` was populated by the agent itself.
  The agent then spent tokens on simulated work instead of real work.

Solution:
  build_success MUST come from run_universal_build(), never from state.
  apply_result.ok MUST be verified via fs-hash before/after, not trusted.
  Anything the agent claims about success must be cross-checked.

This module provides those cross-checks.
"""

import hashlib
import os
from typing import Optional


def fs_hash(path: str) -> str:
    """SHA-256 of file content, or empty string if file doesn't exist."""
    if not os.path.exists(path):
        return ""
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return ""


def verify_file_changed(path: str, before_hash: str) -> tuple[bool, str]:
    """
    Verify that the file actually changed after the agent claimed a write.

    Returns:
      (changed, current_hash)
    """
    after_hash = fs_hash(path)
    changed = (after_hash != before_hash)
    return changed, after_hash


def verify_compiler_output(
    compiler_output_from_state: str,
    actual_compiler_output: str,
) -> dict:
    """
    Cross-check what state says vs what the compiler actually said.

    In a healthy run, these match. In a confused-deputy attack, they diverge.

    Returns:
      {"match": bool, "state_claimed": str, "actual": str, "tampered": bool}
    """
    # Normalize whitespace for comparison
    state_norm = (compiler_output_from_state or "").strip()
    actual_norm = (actual_compiler_output or "").strip()

    match = state_norm == actual_norm
    tampered = bool(state_norm) and bool(actual_norm) and not match

    return {
        "match": match,
        "state_claimed": state_norm[:200],
        "actual": actual_norm[:200],
        "tampered": tampered,
    }


def verify_build_result(
    claimed_success: bool,
    actual_success: bool,
    actual_log: str,
) -> dict:
    """
    Cross-check what state/build_success says vs what run_universal_build returned.

    Returns:
      {"consistent": bool, "trusted_success": bool, "warnings": [str]}
    """
    warnings = []

    if claimed_success != actual_success:
        warnings.append(
            f"build_success mismatch: state={claimed_success} "
            f"but run_universal_build={actual_success}"
        )

    trusted_success = actual_success  # Only trust run_universal_build

    if claimed_success and not actual_success:
        warnings.append(
            "AGENT CLAIMED SUCCESS BUT COMPILER FAILED. "
            "This is the 'simulated test passed' incident pattern."
        )

    return {
        "consistent": claimed_success == actual_success,
        "trusted_success": trusted_success,
        "warnings": warnings,
    }


__all__ = [
    "fs_hash",
    "verify_file_changed",
    "verify_compiler_output",
    "verify_build_result",
]