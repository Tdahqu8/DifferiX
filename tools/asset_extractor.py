"""
tools/asset_extractor.py
from plugin: webdev
------------------------
Extract DifferiX components (.md) from local HTML "part" files.

Designed for the parts/*.html convention used by web projects: each part
is a self-contained HTML file that includes the component's markup, its
own <style> block, and (optionally) a link to a shared stylesheet plus
a <script> block.

What it does:
  1. Reads the HTML file.
  2. Extracts body content (ignoring <head> metadata).
  3. Extracts <style> blocks — from <head> AND from <body>.
  4. Extracts <script> blocks.
  5. Reads any <link rel="stylesheet"> targets if they exist locally.
  6. Filters the linked CSS to only rules whose selectors reference
     classes present in the body.
  7. Emits a component .md with frontmatter, HTML, CSS, and JS.

What it does NOT do (v1):
  - Rename classes
  - Resolve inline styles
  - Split multi-component files
  - Infer `requires` dependencies from class references

Public API:
    extract_component(input_path, output_dir, name=None) -> ComponentResult
    extract_all(input_dir, output_dir, glob="*.html") -> List[ComponentResult]

CLI:
    python tools/asset_extractor.py parts/ -o components/
    python tools/asset_extractor.py parts/cta-btn.html -o components/
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field, asdict
from html.parser import HTMLParser
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------
@dataclass
class ComponentResult:
    success: bool
    component_id: str = ""
    output_path: str = ""
    markdown: str = ""
    classes: List[str] = field(default_factory=list)
    css_rules_kept: int = 0
    css_rules_total: int = 0
    js_blocks: int = 0
    source: str = ""
    error: str = ""

    def as_dict(self) -> Dict:
        return asdict(self)


class ExtractionError(Exception):
    pass


# ---------------------------------------------------------------------------
# HTML parser
# ---------------------------------------------------------------------------
class _PartParser(HTMLParser):
    """
    Extract the pieces we care about from a parts/*.html file:
      - <style> blocks (head and body)
      - <script> blocks
      - <link rel="stylesheet" href="..."> targets
      - <body> inner content
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.styles: List[str] = []
        self.scripts: List[str] = []
        self.stylesheet_hrefs: List[str] = []
        self.body_content: str = ""

        self._in_style = False
        self._style_buffer: List[str] = []

        self._in_script = False
        self._script_buffer: List[str] = []

        self._in_body = False
        self._body_depth = 0
        self._body_start: Optional[int] = None
        self._raw_html: str = ""

    def feed(self, data: str) -> None:
        self._raw_html = data
        super().feed(data)

    # ---- start tags ----
    def handle_starttag(self, tag: str, attrs) -> None:
        tag_lower = tag.lower()
        attr_dict = {k.lower(): (v or "") for k, v in attrs}

        if tag_lower == "style":
            self._in_style = True
            self._style_buffer = []
        elif tag_lower == "script":
            # ignore external scripts — we only keep inline JS
            if "src" not in attr_dict:
                self._in_script = True
                self._script_buffer = []
        elif tag_lower == "link":
            rel = attr_dict.get("rel", "").lower()
            href = attr_dict.get("href", "")
            if "stylesheet" in rel and href:
                self.stylesheet_hrefs.append(href)
        elif tag_lower == "body":
            self._in_body = True
            self._body_depth = 1
            self._body_start = self.getpos()

    def handle_endtag(self, tag: str) -> None:
        tag_lower = tag.lower()
        if tag_lower == "style" and self._in_style:
            self.styles.append("".join(self._style_buffer))
            self._in_style = False
        elif tag_lower == "script" and self._in_script:
            self.scripts.append("".join(self._script_buffer))
            self._in_script = False
        elif tag_lower == "body":
            self._in_body = False

    def handle_data(self, data: str) -> None:
        if self._in_style:
            self._style_buffer.append(data)
        elif self._in_script:
            self._script_buffer.append(data)


def _extract_body_content(html: str) -> str:
    """
    Extract the inner content of <body>...</body>.

    Uses a brace-aware walk rather than a regex so it survives nested
    <body> strings in scripts. Returns "" if no <body> tag is found.
    """
    m = re.search(r"<body\b[^>]*>", html, re.IGNORECASE)
    if not m:
        return ""
    start = m.end()
    m2 = re.search(r"</body\s*>", html[start:], re.IGNORECASE)
    if not m2:
        return html[start:]
    end = start + m2.start()
    return html[start:end].strip()


# ---------------------------------------------------------------------------
# Class name extraction
# ---------------------------------------------------------------------------
_CLASS_ATTR_RE = re.compile(r'class\s*=\s*"([^"]*)"', re.IGNORECASE)
_CLASS_ATTR_RE_SINGLE = re.compile(r"class\s*=\s*'([^']*)'", re.IGNORECASE)
_CSS_CLASS_IN_SELECTOR = re.compile(r"\.([a-zA-Z_][\w-]*)")


def _classes_in_html(html: str) -> Set[str]:
    out: Set[str] = set()
    for pat in (_CLASS_ATTR_RE, _CLASS_ATTR_RE_SINGLE):
        for m in pat.finditer(html):
            for tok in m.group(1).split():
                tok = tok.strip()
                if tok:
                    out.add(tok)
    return out


_JS_CLASS_PATTERNS = (
    re.compile(r"""querySelector(?:All)?\(\s*['"]([^'"]+)['"]"""),
    re.compile(r"""classList\.(?:add|remove|toggle|contains)\(\s*['"]([^'"]+)['"]"""),
    re.compile(r"""className\s*=\s*['"]([^'"]+)['"]"""),
    re.compile(r"""class\s*=\s*\\?['"]([a-zA-Z][\w\s-]*)\\?['"]""", re.IGNORECASE),
)


def _classes_in_js(js: str) -> Set[str]:
    """Extract class names referenced by JS through common patterns."""
    out: Set[str] = set()
    for pattern in _JS_CLASS_PATTERNS:
        for m in pattern.finditer(js):
            token = m.group(1)
            # querySelector returns a selector string; pull out `.class` tokens
            for cls in _CSS_CLASS_IN_SELECTOR.findall(token):
                out.add(cls)
            # className and class="..." return a raw list of class names
            for cls in re.split(r"\s+", token):
                cls = cls.strip()
                if cls and not cls.startswith("."):
                    # only if it looks like a class (no dots, no brackets)
                    if re.fullmatch(r"[a-zA-Z][\w-]*", cls):
                        out.add(cls)
    return out


# ---------------------------------------------------------------------------
# CSS parsing (brace-aware, handles @media correctly)
# ---------------------------------------------------------------------------
def _strip_css_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)


def _walk_css_rules(css: str) -> List[Tuple[str, str]]:
    """
    Return a flat list of (selector, body) pairs. @media blocks are
    recursed into and their inner rules are prefixed with the media query.
    """
    out: List[Tuple[str, str]] = []
    i = 0
    n = len(css)
    while i < n:
        while i < n and css[i].isspace():
            i += 1
        if i >= n:
            break
        start = i
        while i < n and css[i] != "{":
            i += 1
        if i >= n:
            break
        selector = css[start:i].strip()
        # find matching close brace
        depth = 1
        j = i + 1
        while j < n and depth > 0:
            if css[j] == "{":
                depth += 1
            elif css[j] == "}":
                depth -= 1
            j += 1
        body = css[i + 1 : j - 1]
        # @media / @supports: recurse
        if selector.startswith("@"):
            inner = _walk_css_rules(body)
            for inner_sel, inner_body in inner:
                out.append((f"{selector} {{ {inner_sel} }}", inner_body))
        else:
            out.append((selector, body.strip()))
        i = j
    return out


def _filter_css_by_classes(
    css: str, class_set: Set[str]
) -> Tuple[str, int, int]:
    """
    Return (filtered_css, kept_count, total_count).

    A rule is kept if any class referenced in its selector is in class_set.
    Rules with identical selectors are merged into a single rule with
    concatenated bodies.
    """
    rules = _walk_css_rules(_strip_css_comments(css))
    total = len(rules)

    # Preserve order; merge on exact selector match
    ordered_selectors: List[str] = []
    bodies_by_selector: Dict[str, List[str]] = {}

    for selector, body in rules:
        refs = set(_CSS_CLASS_IN_SELECTOR.findall(selector))
        if not (refs & class_set):
            continue
        if selector not in bodies_by_selector:
            ordered_selectors.append(selector)
            bodies_by_selector[selector] = []
        bodies_by_selector[selector].append(body)

    kept = sum(len(v) for v in bodies_by_selector.values())

    # Reconstruct with merged bodies and normalized indentation
    output_lines: List[str] = []
    for selector in ordered_selectors:
        merged_body = "\n".join(bodies_by_selector[selector])
        normalized = _normalize_css_body(merged_body)
        output_lines.append(f"{selector} {{\n{normalized}\n}}")

    return "\n\n".join(output_lines), kept, total


def _normalize_css_body(body: str) -> str:
    """
    Rewrite a CSS body block with consistent 2-space indentation.
    Original bodies often have mixed tabs/spaces and leftover indentation
    from their source file.
    """
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    return "\n".join(f"  {line}" for line in lines)


# ---------------------------------------------------------------------------
# Category inference
# ---------------------------------------------------------------------------
_CATEGORY_HINTS: List[Tuple[str, List[str]]] = [
    # More specific categories first. Each hint checks against the
    # component's source filename and its <body> content.
    ("footer",    ["site-footer", "footer-container"]),
    ("nav",       ["nav-search", "nav-links", "nav-hamburger", "navbar", "<nav"]),
    ("language",  ["custom-lang-selector", "lang-dropdown", "googtrans"]),
    ("form",      ["form-input", "form-label", "form-row"]),
    ("hero",      ["hero-title", "hero-split", "hero-section", "hero-headline"]),
    ("card",      ["feature-card", "card-icon", "card-title"]),
    ("data",      ["result-row", "result-label", "result-output"]),
    ("button",    ["cta-btn", "btn-primary", "btn-secondary"]),
]


def _infer_category(source_name: str, html: str) -> str:
    hay = (source_name + " " + html).lower()
    for cat, keywords in _CATEGORY_HINTS:
        if any(k in hay for k in keywords):
            return cat
    return "component"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "component"


def extract_component(
    input_path: str,
    output_dir: str = "components",
    name: Optional[str] = None,
) -> ComponentResult:
    """
    Extract a single parts/*.html file into a .md component.
    Never raises — check .success and .error.
    """
    result = ComponentResult(success=False, source=input_path)

    path = Path(input_path)
    if not path.exists():
        result.error = f"Input file does not exist: {input_path}"
        return result

    try:
        html = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        result.error = f"Failed to read {input_path}: {exc}"
        return result

    parser = _PartParser()
    try:
        parser.feed(html)
    except Exception as exc:
        logger.debug("HTML parse partial: %s", exc)

    body_content = _extract_body_content(html)
    if not body_content.strip():
        result.error = f"No <body> content found in {input_path}"
        return result

    classes = sorted(_classes_in_html(body_content))
    result.classes = classes

    # ---- Collect CSS sources ----
    css_parts: List[str] = []

    # inline <style> blocks (head + body)
    css_parts.extend(parser.styles)

    # linked local stylesheets
    input_dir = path.parent
    for href in parser.stylesheet_hrefs:
        if href.startswith(("http://", "https://", "//")):
            continue
        candidate = (input_dir / href).resolve()
        if candidate.exists() and candidate.is_file():
            try:
                css_parts.append(candidate.read_text(encoding="utf-8"))
            except OSError:
                pass

    raw_css = "\n\n".join(css_parts)

    # ---- JS blocks ----
    inline_js = "\n\n".join(s.strip() for s in parser.scripts if s.strip())
    result.js_blocks = len([s for s in parser.scripts if s.strip()])

    # JS-referenced classes must be included so that rules for classes
    # injected at runtime (e.g. .sri rows into .search-dropdown) survive
    # the CSS filter.
    js_classes = _classes_in_js(inline_js)
    all_classes_for_css = set(classes) | js_classes

    # (re-filter CSS with the expanded class set)
    filtered_css, kept, total = _filter_css_by_classes(raw_css, all_classes_for_css)
    result.css_rules_kept = kept
    result.css_rules_total = total

    # ---- Frontmatter + markdown ----
    component_id = name or _slug(path.stem)
    category = _infer_category(path.stem, body_content)
    tags = _infer_tags(component_id, category, classes)

    md_lines: List[str] = [
        "---",
        f"id: {component_id}",
        f"category: {category}",
        f"tags: [{', '.join(tags)}]",
        f"source: {path.name}",
        f"classes: [{', '.join(classes)}]",
        "---",
        "",
        f"# {component_id}",
        "",
        "## HTML",
        "",
        "```html",
        body_content.strip(),
        "```",
        "",
        "## CSS",
        "",
        "```css",
        filtered_css.strip() if filtered_css.strip() else "/* no matching CSS rules */",
        "```",
    ]

    if inline_js:
        md_lines.extend(["", "## JavaScript", "", "```javascript", inline_js.strip(), "```"])

    md_lines.append("")
    markdown = "\n".join(md_lines)

    # ---- Write output ----
    try:
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{component_id}.md"
        out_path.write_text(markdown, encoding="utf-8")
        result.output_path = str(out_path)
    except OSError as exc:
        result.error = f"Failed to write output: {exc}"
        return result

    result.markdown = markdown
    result.component_id = component_id
    result.success = True
    return result


def _infer_tags(component_id: str, category: str, classes: List[str]) -> List[str]:
    """Infer 3-6 tags from id, category, and top classes."""
    tags = {category}
    for chunk in component_id.split("-"):
        if len(chunk) > 2:
            tags.add(chunk)
    for cls in classes[:4]:
        for chunk in cls.split("-"):
            if len(chunk) > 2 and chunk not in ("the", "and"):
                tags.add(chunk)
    return sorted(tags)[:8]


def extract_all(
    input_dir: str,
    output_dir: str = "components",
    glob: str = "*.html",
) -> List[ComponentResult]:
    """Extract every *.html file in input_dir."""
    results: List[ComponentResult] = []
    src = Path(input_dir)
    if not src.is_dir():
        logger.error("Not a directory: %s", input_dir)
        return results

    for path in sorted(src.glob(glob)):
        logger.info("Extracting %s", path.name)
        results.append(extract_component(str(path), output_dir=output_dir))

    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _cli() -> int:
    parser = argparse.ArgumentParser(
        description="Extract DifferiX .md components from local HTML part files.",
    )
    parser.add_argument(
        "input",
        help="A single .html file or a directory containing *.html files",
    )
    parser.add_argument(
        "-o", "--output-dir", default="components",
        help="Directory to write .md files (default: components)",
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    inp = Path(args.input)
    if inp.is_dir():
        results = extract_all(str(inp), output_dir=args.output_dir)
    else:
        results = [extract_component(str(inp), output_dir=args.output_dir)]

    ok = 0
    for r in results:
        if r.success:
            ok += 1
            print(
                f"✓ {r.component_id:24s} → {r.output_path}  "
                f"({r.css_rules_kept}/{r.css_rules_total} css rules, "
                f"{r.js_blocks} js blocks)"
            )
        else:
            print(f"✗ {r.source}: {r.error}", file=sys.stderr)

    print(f"\n{ok}/{len(results)} extracted.")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(_cli())