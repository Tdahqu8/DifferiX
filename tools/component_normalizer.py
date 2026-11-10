"""
tools/component_normalizer.py
from plugin:webdev
------------------------------
Rewrite extracted component .md files so their CSS uses the DifferiX
palette contract instead of site-specific token names.

Mapping (CalCook → DifferiX):
  --clr-primary  →  --accent
  --clr-dark     →  --text-main
  --clr-muted    →  --text-muted
  --clr-gray     →  --text-dim
  --clr-white    →  --bg-primary
  --ff-label     →  --font-heading
  --ff-body      →  --font-body

The original token is preserved as a fallback inside var() so the
component still works on a project that only has the site's tokens:
    background: var(--accent, var(--clr-primary));

Not modified: HTML, JS, and any tokens not in the map.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path
from typing import Dict, List

logger = logging.getLogger(__name__)


TOKEN_MAP: Dict[str, str] = {
    "--clr-primary": "--accent",
    "--clr-dark":    "--text-main",
    "--clr-muted":   "--text-muted",
    "--clr-gray":    "--text-dim",
    "--clr-white":   "--bg-primary",
    "--ff-label":    "--font-heading",
    "--ff-body":     "--font-body",
}


def _rewrite_css_uses(css: str) -> str:
    """
    Rewrite `var(--clr-primary)` → `var(--accent, var(--clr-primary))`.
    Leaves declarations like `--clr-primary: #ffd896` untouched (those
    are definitions, not uses).
    """
    for old, new in TOKEN_MAP.items():
        # Match var(--old) but not var(--new, var(--old)) — avoid double-wrap
        pattern = re.compile(r"var\(\s*" + re.escape(old) + r"\s*\)")
        css = pattern.sub(f"var({new}, {old})", css)
    return css


def _extract_css_block(md: str) -> str:
    m = re.search(r"```css\n(.*?)\n```", md, flags=re.DOTALL)
    return m.group(1) if m else ""


def _replace_css_block(md: str, new_css: str) -> str:
    return re.sub(
        r"```css\n.*?\n```",
        f"```css\n{new_css}\n```",
        md,
        count=1,
        flags=re.DOTALL,
    )


def normalize_file(path: Path) -> bool:
    try:
        md = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.error("Read failed for %s: %s", path, exc)
        return False

    original_css = _extract_css_block(md)
    if not original_css:
        logger.info("No CSS block in %s — skipping", path.name)
        return False

    rewritten_css = _rewrite_css_uses(original_css)
    if rewritten_css == original_css:
        logger.info("No changes for %s", path.name)
        return False

    new_md = _replace_css_block(md, rewritten_css)
    try:
        path.write_text(new_md, encoding="utf-8")
    except OSError as exc:
        logger.error("Write failed for %s: %s", path, exc)
        return False

    return True


def normalize_dir(input_dir: str) -> int:
    count = 0
    for path in sorted(Path(input_dir).glob("*.md")):
        if normalize_file(path):
            count += 1
            print(f"✓ {path.name}")
    return count


def _cli() -> int:
    parser = argparse.ArgumentParser(
        description="Normalize extracted component CSS to DifferiX tokens.",
    )
    parser.add_argument("input", help="A directory of .md components")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(message)s",
    )

    n = normalize_dir(args.input)
    print(f"\n{n} file(s) rewritten.")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())