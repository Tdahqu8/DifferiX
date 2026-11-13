"""
tools/security/behavior_guard.py
--------------------------------
Detects runaway agent behavior (context bloat, repeated actions,
path escapes) before it burns tokens or compromises the sandbox.

This is the direct fix for the August 2026 incident where a looping agent
kept appending files to its context, eventually hallucinating success.
"""

import os
import time
import hashlib


class BehaviorGuard:
    def __init__(self):
        self.recent_attempts = []
        self.context_size_history = []

    def _get_action_signature(self, state: dict, meta) -> str:
        """Create a unique fingerprint of what the agent just did."""
        target = str(state.get("target_file") or "")
        # Use the agent's raw output (or the generated code) as the payload hash
        raw = ""
        if hasattr(meta, "raw_response"):
            raw = str(meta.raw_response)
        elif isinstance(state.get("generated_code"), dict):
            raw = str(state["generated_code"].get("code", ""))
        
        payload_hash = hashlib.md5(raw.encode("utf-8")).hexdigest()
        return f"{target}:{payload_hash}"

    def observe_attempt(self, state: dict, meta) -> str | None:
        """
        Returns a reason string if we should HALT the pipeline, else None.
        """
        # 1. Context Bloat Detection
        prompt_tokens = getattr(meta, "prompt_token_count", 0) or 0
        self.context_size_history.append(prompt_tokens)
        
        if len(self.context_size_history) >= 3:
            last3 = self.context_size_history[-3:]
            # If context grows strictly upward for 3 attempts (e.g., 8k -> 15k -> 25k)
            if last3[0] < last3[1] < last3[2]:
                return (
                    f"CONTEXT_BLOAT: prompt size growing monotonically "
                    f"({last3[0]} -> {last3[1]} -> {last3[2]} tokens). "
                    f"Agent is likely stuck in a loop adding irrelevant files."
                )

        # 2. Repeated Action Detection
        sig = self._get_action_signature(state, meta)
        self.recent_attempts.append({"sig": sig, "ts": time.time()})
        self.recent_attempts = self.recent_attempts[-5:] # keep last 5
        
        if len(self.recent_attempts) >= 3:
            last3_sigs = [a["sig"] for a in self.recent_attempts[-3:]]
            if len(set(last3_sigs)) == 1:
                return (
                    "REPEATED_ACTION: the exact same patch/code was generated "
                    "3 times in a row. The agent is stuck."
                )

        # 3. Path Escape Detection
        project_root = state.get("project_root", ".")
        target_file = state.get("target_file")
        if target_file:
            abs_target = os.path.abspath(os.path.join(project_root, target_file))
            abs_root = os.path.abspath(project_root)
            
            # Check if target is outside root
            if not abs_target.startswith(abs_root + os.sep) and abs_target != abs_root:
                return f"PATH_ESCAPE: target file '{target_file}' is outside the project root."
            
            # Check for credential files
            sensitive_names = (".env", "id_rsa", ".pem", "credentials", "secrets")
            if any(s in os.path.basename(abs_target).lower() for s in sensitive_names):
                return f"CREDENTIAL_ACCESS: agent tried to modify sensitive file '{target_file}'."

        return None