#!/usr/bin/env python3
"""
patch.py — Simple SEARCH/REPLACE patch applier (no LLM, no pipeline).

Применяет патчи напрямую к файлам, используя ту же логику матчинга
(exact → stripped → fuzzy), что и DifferiX, но без вызова main.py.

Формат патча (можно несколько блоков подряд):

    <<<<<<< SEARCH
    old lines to find
    =======
    new lines to write
    >>>>>>> REPLACE

    <<<<<<< SEARCH
    another block
    =======
    ...
    >>>>>>> REPLACE

Использование:
    python patch.py -f agents/editor.py -p patch.txt
    python patch.py -f agents/editor.py  # затем вставь патч, Ctrl+D
    python patch.py -f agents/editor.py -p patch.txt --dry-run
    python patch.py -f agents/editor.py -p patch.txt -y

Флаги:
    -f, --file      Целевой файл (обязательно)
    -p, --patch     Файл с патчем. Если не задан — читает stdin
    -y, --yes       Не спрашивать подтверждение
    -n, --dry-run   Показать diff, но не писать на диск
    --no-backup     Не создавать .bak файл
    --no-validate   Не запускать py_compile / node --check после записи
    --fuzzy N       Минимальный порог fuzzy-матчинга (по умолчанию 0.85)
"""
import argparse
import difflib
import os
import re
import shutil
import subprocess
import sys
import time

# ---------- ANSI цвета ----------
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"


# ---------- Парсер блоков ----------
def parse_blocks(text: str) -> list:
    """Возвращает список (search_block, replace_block)."""
    pattern = r"<{4,}\s*SEARCH[ \t]*\n(.*?)\n?={4,}[ \t]*\n(.*?)\n?>{4,}\s*REPLACE"
    matches = re.findall(pattern, text, re.DOTALL)
    return [(s, r) for s, r in matches]


# ---------- Матчинг ----------
def find_block(content: str, search: str, fuzzy_threshold: float = 0.85):
    """
    Возвращает (start_char, end_char, method) или (None, None, reason).
    Методы: 'exact', 'stripped', 'fuzzy(NN%)'.
    """
    # 1. Exact match
    if search in content:
        idx = content.find(search)
        return idx, idx + len(search), "exact"

    # 2. Stripped match (normalize trailing whitespace per line)
    search_lines = [ln.rstrip() for ln in search.splitlines()]
    content_lines = [ln.rstrip() for ln in content.splitlines()]

    search_stripped = "\n".join(search_lines)
    content_stripped = "\n".join(content_lines)

    if search_stripped and content_stripped.count(search_stripped) == 1:
        # Need to map back to original char indices
        # Walk line by line to find start index
        search_line_count = len(search_lines)
        for i in range(len(content_lines) - search_line_count + 1):
            window = content_lines[i:i + search_line_count]
            if "\n".join(window) == search_stripped:
                # Compute char offset in original content
                start = sum(len(ln) + 1 for ln in content.splitlines()[:i])
                # End = start + sum of lengths of content_lines[i:i+search_line_count] + newlines
                end = start
                for j in range(i, i + search_line_count):
                    end += len(content.splitlines()[j]) + 1
                end -= 1  # last newline not part of block
                return start, end, "stripped"

    # 3. Fuzzy match
    search_line_count = len(search_lines)
    if search_line_count == 0 or search_line_count > len(content_lines):
        return None, None, "search block too large"

    best_ratio = 0.0
    best_start_line = None
    for i in range(len(content_lines) - search_line_count + 1):
        window = content_lines[i:i + search_line_count]
        ratio = difflib.SequenceMatcher(None, search_stripped, "\n".join(window)).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_start_line = i

    if best_ratio >= fuzzy_threshold and best_start_line is not None:
        start = sum(len(ln) + 1 for ln in content.splitlines()[:best_start_line])
        end = start
        for j in range(best_start_line, best_start_line + search_line_count):
            end += len(content.splitlines()[j]) + 1
        end -= 1
        return start, end, f"fuzzy({best_ratio:.0%})"

    return None, None, f"no match (best fuzzy: {best_ratio:.0%})"


# ---------- Валидация после записи ----------
def validate_file(path: str) -> tuple:
    """Возвращает (ok, error_msg)."""
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".py":
            r = subprocess.run(
                [sys.executable, "-m", "py_compile", path],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode != 0:
                return False, r.stderr[:500]
            return True, ""

        if ext in (".js", ".mjs", ".cjs"):
            try:
                r = subprocess.run(
                    ["node", "--check", path],
                    capture_output=True, text=True, timeout=10,
                )
                if r.returncode != 0:
                    return False, r.stderr[:500]
                return True, ""
            except FileNotFoundError:
                return True, ""

        if ext == ".json":
            import json
            with open(path) as f:
                json.load(f)
            return True, ""

        return True, ""
    except subprocess.TimeoutExpired:
        return False, "validation timeout"
    except Exception as e:
        return False, str(e)


# ---------- Main ----------
def main():
    parser = argparse.ArgumentParser(description="Simple SEARCH/REPLACE patch applier")
    parser.add_argument("-f", "--file", required=True, help="Target file")
    parser.add_argument("-p", "--patch", help="Patch file (default: stdin)")
    parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation")
    parser.add_argument("-n", "--dry-run", action="store_true", help="Show diff, don't write")
    parser.add_argument("--no-backup", action="store_true", help="Don't create .bak")
    parser.add_argument("--no-validate", action="store_true", help="Skip post-write validation")
    parser.add_argument("--fuzzy", type=float, default=0.85, help="Fuzzy threshold (default 0.85)")
    args = parser.parse_args()

    target = args.file
    if not os.path.exists(target):
        print(f"{RED}✗ File not found: {target}{RESET}")
        sys.exit(1)

    # Read patch
    if args.patch:
        try:
            with open(args.patch, "r", encoding="utf-8") as f:
                patch_text = f.read()
        except OSError as e:
            print(f"{RED}✗ Cannot read patch file: {e}{RESET}")
            sys.exit(1)
    else:
        if sys.stdin.isatty():
            print(f"{CYAN}Вставь патч, затем нажми Ctrl+D чтобы закончить...{RESET}")
        patch_text = sys.stdin.read()

    blocks = parse_blocks(patch_text)
    if not blocks:
        print(f"{RED}✗ No SEARCH/REPLACE blocks found in patch{RESET}")
        sys.exit(1)

    print(f"{CYAN}Найдено {len(blocks)} блок(ов). Файл: {target}{RESET}")

    # Read original
    with open(target, "r", encoding="utf-8") as f:
        content = f.read()
    original = content

    # Apply blocks one by one
    applied = 0
    for idx, (search, replace) in enumerate(blocks, 1):
        start, end, method = find_block(content, search, args.fuzzy)
        if start is None:
            print(f"\n{RED}✗ Блок {idx} не найден: {method}{RESET}")
            print(f"{DIM}SEARCH (первые 300 символов):{RESET}")
            print(f"{DIM}{search[:300]}{RESET}")
            print(f"\n{RED}Патч НЕ применён. Файл не изменён.{RESET}")
            sys.exit(1)
        content = content[:start] + replace + content[end:]
        color = GREEN if method == "exact" else YELLOW
        print(f"{color}✓ Блок {idx}: {method}{RESET}")
        applied += 1

    # Show diff
    print(f"\n{CYAN}--- Diff preview ---{RESET}")
    diff = list(difflib.unified_diff(
        original.splitlines(),
        content.splitlines(),
        fromfile=f"a/{target}",
        tofile=f"b/{target}",
        lineterm="",
        n=3,
    ))
    for line in diff[:100]:
        if line.startswith("+") and not line.startswith("+++"):
            print(f"{GREEN}{line}{RESET}")
        elif line.startswith("-") and not line.startswith("---"):
            print(f"{RED}{line}{RESET}")
        elif line.startswith("@@"):
            print(f"{CYAN}{line}{RESET}")
        else:
            print(line)
    if len(diff) > 100:
        print(f"{DIM}... (diff обрезан, показано 100 строк){RESET}")

    if args.dry_run:
        print(f"\n{YELLOW}--dry-run: файл не записан{RESET}")
        return

    # Confirm
    if not args.yes:
        try:
            answer = input(f"\n{BOLD}Применить? [y/N]: {RESET}").strip().lower()
        except (KeyboardInterrupt, EOFError):
            print(f"\n{YELLOW}Отменено{RESET}")
            sys.exit(1)
        if answer not in ("y", "yes"):
            print(f"{YELLOW}Отменено{RESET}")
            sys.exit(0)

    # Backup
    backup_path = None
    if not args.no_backup:
        backup_path = f"{target}.bak.{int(time.time())}"
        shutil.copy2(target, backup_path)
        print(f"{DIM}Backup: {backup_path}{RESET}")

    # Write
    with open(target, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"{GREEN}✓ Записано: {applied} блок(ов){RESET}")

    # Validate
    if not args.no_validate:
        ok, err = validate_file(target)
        if not ok:
            print(f"\n{RED}✗ Валидация провалилась:{RESET}")
            print(f"{RED}{err}{RESET}")
            if backup_path:
                shutil.copy2(backup_path, target)
                print(f"{YELLOW}↺ Файл восстановлен из backup{RESET}")
            sys.exit(1)
        print(f"{GREEN}✓ Валидация пройдена{RESET}")


if __name__ == "__main__":
    main()