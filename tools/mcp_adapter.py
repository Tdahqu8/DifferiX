import subprocess
import json
import threading
import uuid
import logging

logger = logging.getLogger(__name__)

class GodotMCPAdapter:
    def __init__(self):
        # Запускаем npm-мост как подпроцесс
        self.proc = subprocess.Popen(
            ["npx", "-y", "@npgamedev/godot-mcp-server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1
        )
        self._id = 0
        self._responses = {}
        self._lock = threading.Lock()
        threading.Thread(target=self._read_stdout, daemon=True).start()
        
        # Инициализация MCP-сессии
        self._send_request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "DifferiX", "version": "1.0"}
        })
        self._send_notification("notifications/initialized", {})

    def _read_stdout(self):
        for line in self.proc.stdout:
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "id" in msg:
                with self._lock:
                    event = self._responses.pop(msg["id"], None)
                if event:
                    event["response"] = msg
                    event["event"].set()

    def _send_request(self, method, params):
        self._id += 1
        req_id = str(self._id)
        event = {"event": threading.Event(), "response": None}
        with self._lock:
            self._responses[req_id] = event
        payload = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
        self.proc.stdin.write(json.dumps(payload) + "\n")
        self.proc.stdin.flush()
        if not event["event"].wait(timeout=30):
            raise TimeoutError(f"No response for {method}")
        return event["response"]

    def _send_notification(self, method, params):
        payload = {"jsonrpc": "2.0", "method": method, "params": params}
        self.proc.stdin.write(json.dumps(payload) + "\n")
        self.proc.stdin.flush()

    def call_tool(self, tool_name: str, arguments: dict) -> dict:
        try:
            resp = self._send_request("tools/call", {"name": tool_name, "arguments": arguments})
            if "error" in resp:
                return {"ok": False, "error": resp["error"].get("message", str(resp["error"]))}
            result = resp.get("result", {})
            is_error = result.get("isError", False)
            content = result.get("content", [])
            text = "\n".join([c.get("text", "") for c in content if c.get("type") == "text"])
            return {"ok": not is_error, "result": text, "error": text if is_error else None}
        except Exception as e:
            return {"ok": False, "error": str(e)}

# Синглтон
_adapter = None
def get_godot_adapter():
    global _adapter
    if _adapter is None:
        _adapter = GodotMCPAdapter()
    return _adapter