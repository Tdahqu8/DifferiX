import re

def find_dangerous_code(code: str) -> list[str]:
    """
    Detects dangerous code patterns before file write.
    Blocks patterns related to destructive file operations and shell deletion.
    Returns a list of matched dangerous patterns (strings). Empty list means safe.
    """
    # Strip comments to avoid false positives in documentation/comments
    # Strip multi-line comments/docstrings
    cleaned = re.sub(r'""".*?"""', '', code, flags=re.DOTALL)
    cleaned = re.sub(r"'''.*?'''", '', cleaned, flags=re.DOTALL)
    cleaned = re.sub(r'/\*.*?\*/', '', cleaned, flags=re.DOTALL)
    
    # Strip single-line comments
    lines = []
    for line in cleaned.splitlines():
        # Remove lines that are purely comments
        if re.match(r'^\s*(#|//)', line):
            continue
        # Remove inline comments (approximate)
        line_no_comment = re.sub(r'\s+(#|//).*$', '', line)
        lines.append(line_no_comment)
    cleaned_code = "\n".join(lines)

    dangerous_patterns = {
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
        r'os\.rename\s*\(.*\.{2}/': "os.rename with parent directory traversal"
    }

    hits = []
    for pattern, label in dangerous_patterns.items():
        if re.search(pattern, cleaned_code, re.IGNORECASE | re.DOTALL):
            hits.append(label)
            
    return hits

def is_safe(code: str) -> bool:
    """Convenience wrapper: returns True if find_dangerous_code returns empty list."""
    return len(find_dangerous_code(code)) == 0