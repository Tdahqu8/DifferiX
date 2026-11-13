import pytest
from tools.security.behavior_guard import BehaviorGuard

class MockMeta:
    def __init__(self, tokens, raw):
        self.prompt_token_count = tokens
        self.raw_response = raw

@pytest.mark.unit
def test_context_bloat_detection():
    guard = BehaviorGuard()
    state = {"target_file": "main.py"}
    
    assert guard.observe_attempt(state, MockMeta(1000, "a")) is None
    assert guard.observe_attempt(state, MockMeta(2000, "b")) is None
    # Third attempt grows, should trigger
    reason = guard.observe_attempt(state, MockMeta(7000, "c"))
    assert reason is not None
    assert "CONTEXT_BLOAT" in reason

@pytest.mark.unit
def test_repeated_action_detection():
    guard = BehaviorGuard()
    state = {"target_file": "main.py"}
    
    assert guard.observe_attempt(state, MockMeta(1000, "same code")) is None
    assert guard.observe_attempt(state, MockMeta(1000, "same code")) is None
    reason = guard.observe_attempt(state, MockMeta(1000, "same code"))
    assert reason is not None
    assert "REPEATED_ACTION" in reason