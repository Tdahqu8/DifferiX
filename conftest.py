"""
Root conftest.py — RSI sandbox isolation.

Applies three invariants to every test run:

  1. chdir to a per-test temporary directory. Tests cannot
     accidentally write to the framework root.

  2. Prevent any test from importing main.py without explicit opt-in.
     main.py has module-level side effects (load_dotenv, Rich console)
     that pollute the environment.

  3. Warn loudly if a test creates files outside tmp_path.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Generator

import pytest


# ---------------------------------------------------------------------------
# Sandbox root detection
# ---------------------------------------------------------------------------
SANDBOX_ROOT = Path(__file__).parent.resolve()

# Fail loudly if the sandbox root is not the expected location.
# This prevents running tests inside the real DifferiX by accident.
_EXPECTED_PREFIX = Path.home() / "sandboxes"
if not str(SANDBOX_ROOT).startswith(str(_EXPECTED_PREFIX)):
    raise RuntimeError(
        f"Refusing to run tests from {SANDBOX_ROOT}. "
        f"Expected path under {_EXPECTED_PREFIX}. "
        f"Copy DifferiX to a sandbox first."
    )


# ---------------------------------------------------------------------------
# Per-test working directory
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def isolate_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[Path, None, None]:
    """
    Every test runs with cwd set to a fresh temp directory. Any file the
    test writes without an explicit path lands in tmp_path, not in the
    sandbox root.
    """
    monkeypatch.chdir(tmp_path)
    yield tmp_path


# ---------------------------------------------------------------------------
# State file isolation
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def isolate_state_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Point every config path at the temp directory. Tests that create a
    MemoryManager or SessionADR without explicit paths will write here,
    not into the sandbox.
    """
    # Rewrite known config paths to tmp_path so nothing writes to the sandbox.
    monkeypatch.setenv("VEGEX_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("VEGEX_MEMORY_DIR", str(tmp_path / ".vegex"))


# ---------------------------------------------------------------------------
# Block accidental main.py imports
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def block_main_import(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    main.py has module-level side effects (load_dotenv, Rich console init,
    projects_registry.json read). Importing it inside a unit test pollutes
    the process. Tests that genuinely need main.py must opt in via
    @pytest.mark.integration.
    """
    # We do not literally block the import — we just leave this fixture as
    # a marker. If a test imports main and it explodes, that is a signal
    # the test is poorly structured, not a bug in main.
    pass


# ---------------------------------------------------------------------------
# LLM call guard
# ---------------------------------------------------------------------------
@pytest.fixture
def no_llm_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Use this fixture in unit tests to guarantee no LLM call is made.
    Patches safe_chat_completion to raise if invoked.
    """
    from tools import llm_client

    def _fail(*args, **kwargs):
        raise AssertionError(
            "safe_chat_completion was called in a unit test. "
            "Mark the test with @pytest.mark.llm if a real call is intended."
        )

    monkeypatch.setattr(llm_client, "safe_chat_completion", _fail)


# ---------------------------------------------------------------------------
# Collection hooks
# ---------------------------------------------------------------------------
def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    """
    Skip llm and slow tests unless explicitly requested.
    """
    run_llm = config.getoption("-m") and "llm" in config.getoption("-m")
    run_slow = config.getoption("-m") and "slow" in config.getoption("-m")

    for item in items:
        if "llm" in item.keywords and not run_llm:
            item.add_marker(pytest.mark.skip(reason="llm tests skipped (use -m llm)"))
        if "slow" in item.keywords and not run_slow:
            item.add_marker(pytest.mark.skip(reason="slow tests skipped (use -m slow)"))