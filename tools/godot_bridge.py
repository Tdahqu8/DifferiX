"""
tools/godot_bridge.py
---------------------
Thin MCP client adapter for DifferiX → Godot MCP Server.

Translates DifferiX-style operations into MCP JSON-RPC tool calls
and back. Runs as a persistent background connection.

Usage:
    from tools.godot_bridge import GodotBridge
    bridge = GodotBridge()  # auto-connects on first use
    result = bridge.call_tool("godot_add_node", {"parent_path": "/root", "node_type": "Sprite2D", "node_name": "Player"})
"""

import json
import logging
import os
import socket
import threading
import time
import uuid
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

DEFAULT_HOST = os.getenv("GODOT_MCP_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.getenv("GODOT_MCP_PORT", "6510"))
DEFAULT_TIMEOUT = float(os.getenv("GODOT_MCP_TIMEOUT", "30"))


class GodotBridgeError(Exception):
    pass


class GodotBridge:
    """
    Singleton-per-process client for a Godot MCP server.

    The MCP server speaks JSON-RPC 2.0 over a raw TCP socket (or WebSocket,
    depending on the implementation — check your server's docs). We support
    raw newline-delimited JSON for the Community Edition.
    """

    _instance: Optional["GodotBridge"] = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self, host: str = None, port: int = None, timeout: float = None):
        if self._initialized:
            return
        self.host = host or DEFAULT_HOST
        self.port = port or DEFAULT_PORT
        self.timeout = timeout or DEFAULT_TIMEOUT
        self._sock: Optional[socket.socket] = None
        self._reader_thread: Optional[threading.Thread] = None
        self._pending: Dict[str, dict] = {}
        self._pending_lock = threading.Lock()
        self._buffer = b""
        self._initialized = True

    # ------------------------------------------------------------------ #
    # Connection management
    # ------------------------------------------------------------------ #
    def _connect(self) -> None:
        if self._sock is not None:
            return
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self.timeout)
            sock.connect((self.host, self.port))
            self._sock = sock
            self._reader_thread = threading.Thread(
                target=self._reader_loop, daemon=True
            )
            self._reader_thread.start()
            logger.info(f"[GodotBridge] Connected to {self.host}:{self.port}")
        except (ConnectionRefusedError, socket.timeout, OSError) as exc:
            raise GodotBridgeError(
                f"Cannot connect to Godot MCP server at {self.host}:{self.port}. "
                f"Is the MCP server running? Error: {exc}"
            )

    def _reader_loop(self) -> None:
        """Read newline-delimited JSON messages from MCP server."""
        try:
            while self._sock:
                chunk = self._sock.recv(65536)
                if not chunk:
                    break
                self._buffer += chunk
                while b"\n" in self._buffer:
                    line, self._buffer = self._buffer.split(b"\n", 1)
                    if not line.strip():
                        continue
                    try:
                        msg = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    self._dispatch_message(msg)
        except Exception as exc:
            logger.warning(f"[GodotBridge] Reader loop ended: {exc}")
        finally:
            with self._pending_lock:
                for req_id, pending in self._pending.items():
                    pending["error"] = "Connection closed"
                    pending["event"].set()
                self._pending.clear()

    def _dispatch_message(self, msg: dict) -> None:
        req_id = msg.get("id")
        if req_id is None:
            return
        with self._pending_lock:
            pending = self._pending.pop(req_id, None)
        if pending:
            pending["response"] = msg
            pending["event"].set()

    # ------------------------------------------------------------------ #
    # RPC
    # ------------------------------------------------------------------ #
    def _rpc(self, method: str, params: dict) -> dict:
        self._connect()
        req_id = str(uuid.uuid4())
        event = threading.Event()
        with self._pending_lock:
            self._pending[req_id] = {"event": event, "response": None, "error": None}

        payload = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params,
        }
        try:
            self._sock.sendall((json.dumps(payload) + "\n").encode("utf-8"))
        except OSError as exc:
            with self._pending_lock:
                self._pending.pop(req_id, None)
            raise GodotBridgeError(f"Send failed: {exc}")

        if not event.wait(timeout=self.timeout):
            with self._pending_lock:
                self._pending.pop(req_id, None)
            raise GodotBridgeError(f"Timeout waiting for response to {method}")

        with self._pending_lock:
            pending = self._pending.pop(req_id, None) or {}
        if pending.get("error"):
            raise GodotBridgeError(pending["error"])
        return pending.get("response") or {}

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def call_tool(self, tool_name: str, arguments: dict) -> dict:
        """
        Invoke an MCP tool by name. Returns {"ok": bool, "result": Any, "error": str|None}.
        """
        try:
            resp = self._rpc("tools/call", {
                "name": tool_name,
                "arguments": arguments,
            })
        except GodotBridgeError as exc:
            return {"ok": False, "result": None, "error": str(exc)}

        if "error" in resp:
            return {
                "ok": False, "result": None,
                "error": resp["error"].get("message", str(resp["error"])),
            }
        result = resp.get("result", {})
        # MCP tool results are wrapped: {"content": [{"type": "text", "text": "..."}], "isError": bool}
        is_error = result.get("isError", False)
        content = result.get("content", [])
        text_parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text_parts.append(item.get("text", ""))
        combined = "\n".join(text_parts)
        return {"ok": not is_error, "result": combined, "error": combined if is_error else None}

    def list_tools(self) -> list:
        """Return available MCP tools (useful for agent discovery)."""
        try:
            resp = self._rpc("tools/list", {})
            return resp.get("result", {}).get("tools", [])
        except GodotBridgeError:
            return []

    def close(self) -> None:
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None


# Convenience singleton accessor
_default_bridge: Optional[GodotBridge] = None


def get_bridge() -> GodotBridge:
    global _default_bridge
    if _default_bridge is None:
        _default_bridge = GodotBridge()
    return _default_bridge