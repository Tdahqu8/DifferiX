import re

def find_dangerous_code(code: str) -> list[str]:
    """
    Detects dangerous code patterns and workspace inspection exploits before file write.
    """
    cleaned = re.sub(r'""".*?"""', '', code, flags=re.DOTALL)
    cleaned = re.sub(r"'''.*?'''", '', cleaned, flags=re.DOTALL)
    cleaned = re.sub(r'/\*.*?\*/', '', cleaned, flags=re.DOTALL)
    
    lines = []
    for line in cleaned.splitlines():
        if re.match(r'^\s*(#|//)', line):
            continue
        line_no_comment = re.sub(r'\s+(#|//).*$', '', line)
        lines.append(line_no_comment)
    cleaned_code = "\n".join(lines)

    dangerous_patterns = {
        # --- Existing Destructive Operations ---
        r'os\.remove\s*\(': "os.remove()",
        r'os\.unlink\s*\(': "os.unlink()",
        r'shutil\.rmtree\s*\(': "shutil.rmtree()",
        r'\.unlink\s*\(': "Path.unlink()",
        r'os\.system\s*\(': "os.system()",
        r'rm\s+-rf': "rm -rf",
        r'Remove-Item\s+.*-Recurse': "Remove-Item -Recurse",
        r'Remove-Item\s+-Recurse': "Remove-Item -Recurse",
        r'subprocess\.(run|Popen|call|check_output|check_call)\s*\(.*shell\s*=\s*True': "subprocess with shell=True",
        r'del\s+/s': "del /s",
        r'shutil\.move\s*\(.*\.{2}/': "shutil.move with parent directory traversal",
        r'os\.rename\s*\(.*\.{2}/': "os.rename with parent directory traversal",

        # --- Context Dumping & Inspection Exploits ---
        r'def\s+test_.*inspect': "Inspection test definition (test_inspect)",
        r'raise\s+Exception\s*\(.*(os\.listdir|glob|read|open)': "Raising exception to dump workspace files",
        r'assert\s+False\s*,\s*.*(inspect|file|workspace|dir)': "Intentional assert failure to dump workspace context",
        r'sys\.stderr\.write\s*\(.*open\s*\(': "Writing file content to stderr for log leakage",
        r'print\s*\(\s*open\s*\(.*\.read\s*\(\s*\)\s*\)': "Direct file print dumping"
    }

    hits = []
    for pattern, label in dangerous_patterns.items():
        if re.search(pattern, cleaned_code, re.IGNORECASE | re.DOTALL):
            hits.append(label)
            
    return hits

def is_safe(code: str) -> bool:
    return len(find_dangerous_code(code)) == 0