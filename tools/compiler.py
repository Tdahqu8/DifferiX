import os
import subprocess
import sys
from typing import Dict, Any

class PolyglotTestRunner:
    """Executes tests across multiple programming environments."""

    CONFIGS = {
        "python": {
            "test_dir": "tests",
            "file_ext": "_test.py",
            "cmd": lambda test_file: [sys.executable, "-m", "pytest", test_file, "-v"]
        },
        "javascript": {
            "test_dir": "__tests__",
            "file_ext": ".test.js",
            "cmd": lambda test_file: ["npx", "jest", test_file]
        },
        "typescript": {
            "test_dir": "__tests__",
            "file_ext": ".test.ts",
            "cmd": lambda test_file: ["npx", "vitest", "run", test_file]
        },
        "csharp": {
            "test_dir": "Tests",
            "file_ext": "Tests.cs",
            "cmd": lambda test_file: ["dotnet", "test", "--filter", os.path.basename(test_file)]
        },
        "cpp": {
            "test_dir": "tests",
            "file_ext": "_test.cpp",
            "cmd": lambda test_file: ["ctest", "--output-on-failure"]
        }
    }

    def run_universal_build(self, language: str, test_file_path: str, timeout: int = 15) -> Dict[str, Any]:
        lang = language.lower()
        if lang not in self.CONFIGS:
            return {"success": False, "error": f"Unsupported language: {language}"}

        cmd_builder = self.CONFIGS[lang]["cmd"]
        cmd = cmd_builder(test_file_path)

        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout
            )
            return {
                "success": res.returncode == 0,
                "exit_code": res.returncode,
                "stdout": res.stdout,
                "stderr": res.stderr
            }
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "exit_code": -1,
                "stdout": "",
                "stderr": f"Test execution timed out after {timeout}s (Possible infinite loop/hang)."
            }
        except FileNotFoundError as e:
            return {
                "success": False,
                "exit_code": -1,
                "stdout": "",
                "stderr": f"Required test runner tool binary not found in system PATH: {str(e)}"
            }


def run_universal_build(project_path: str, language: str, target_file: str = "") -> tuple[bool, str]:
    """
    Top-level wrapper expected by main.py.
    Unpacks PolyglotTestRunner result into a (success, log) tuple.
    """
    runner = PolyglotTestRunner()

    if not target_file:
        lang_cfg = runner.CONFIGS.get(language.lower(), {})
        test_dir = lang_cfg.get("test_dir", "tests")
        target_file = os.path.join(project_path, test_dir)

    res = runner.run_universal_build(language=language, test_file_path=target_file)

    success = res.get("success", False)
    stdout = res.get("stdout", "")
    stderr = res.get("stderr", "")
    error = res.get("error", "")

    log = stdout
    if stderr:
        log += f"\n[STDERR]\n{stderr}"
    if error:
        log += f"\n[ERROR]\n{error}"

    return success, log.strip()