"""
tools/coherence_checker.py
---------------------------
Detects cross-file coherence violations in web projects.

Given a set of files (HTML, CSS, JS), reports:
  - HTML classes that have no matching CSS rule (unstyled markup)
  - CSS class selectors that match no HTML class and are not added by JS
  - <script src>, <link href>, <img src>, and CSS url() references
    that point to files which don't exist on disk
  - CSS custom properties used but never defined
  - JS querySelector / getElementById calls that target non-existent
    classes or IDs in HTML
  - JS classList.add / className assignments that add classes with no
    CSS styling

The JS checks are the key architectural addition: agent context windows
routinely produce JS that queries classes the HTML never had. Static
analysis catches these mistakes deterministically without relying on any
LLM to remember the original markup.

Public API:
  - check_coherence(project_root, files) -> CoherenceReport
  - render_report(report) -> str
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Safe tokens: never report these as orphan or missing
# ---------------------------------------------------------------------------
_SAFE_CSS_SELECTORS: Set[str] = {
    "clearfix", "visually-hidden", "sr-only", "container", "wrapper",
    "hidden", "visible", "active", "focus", "hover",
}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CoherenceIssue:
    kind: str
    detail: str
    severity: str  # "error" | "warning"
    source_file: str = ""

    def as_line(self) -> str:
        loc = f" [{self.source_file}]" if self.source_file else ""
        return f"{self.severity.upper()}: {self.kind}{loc}: {self.detail}"


@dataclass
class CoherenceReport:
    html_classes: Set[str] = field(default_factory=set)
    html_ids: Set[str] = field(default_factory=set)
    css_selectors: Set[str] = field(default_factory=set)
    css_vars_defined: Set[str] = field(default_factory=set)
    css_vars_used: Set[str] = field(default_factory=set)
    js_classes_queried: Set[str] = field(default_factory=set)
    js_ids_queried: Set[str] = field(default_factory=set)
    js_classes_added: Set[str] = field(default_factory=set)
    issues: List[CoherenceIssue] = field(default_factory=list)
    files_checked: List[str] = field(default_factory=list)
    skipped_reason: Optional[str] = None
    notes: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(i.severity == "error" for i in self.issues)

    @property
    def errors(self) -> List[CoherenceIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> List[CoherenceIssue]:
        return [i for i in self.issues if i.severity == "warning"]


# ---------------------------------------------------------------------------
# HTML parsing
# ---------------------------------------------------------------------------
_HTML_CLASS_RE = re.compile(r'class\s*=\s*"([^"]*)"', re.IGNORECASE)
_HTML_CLASS_RE_SINGLE = re.compile(r"class\s*=\s*'([^']*)'", re.IGNORECASE)
_VALID_CLASS_TOKEN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_-]*$")
_HTML_SCRIPT_BLOCK = re.compile(r"<script\b[^>]*>.*?</script>", re.DOTALL | re.IGNORECASE)
_HTML_STYLE_BLOCK = re.compile(r"<style\b[^>]*>.*?</style>", re.DOTALL | re.IGNORECASE)
_TAILWIND_MARKERS = re.compile(r"(?:tailwindcss|tailwind\s*cdn|@tailwind\s+|tailwind\.config)", re.IGNORECASE)
_HTML_ID_RE = re.compile(r'\bid\s*=\s*"([^"]*)"', re.IGNORECASE)
_HTML_ID_RE_SINGLE = re.compile(r"id\s*=\s*'([^']*)'", re.IGNORECASE)
_HTML_DATA_TAB = re.compile(r'data-tab\s*=\s*(["\'])([^"\']+)\1', re.IGNORECASE)
_HTML_SRC_RE = re.compile(r'\bsrc\s*=\s*"([^"]+)"', re.IGNORECASE)
_HTML_HREF_RE = re.compile(r'\bhref\s*=\s*"([^"]+)"', re.IGNORECASE)
_HTML_INLINE_SCRIPT_RE = re.compile(
    r"<script(?![^>]*\bsrc\s*=)[^>]*>(.*?)</script>",
    re.DOTALL | re.IGNORECASE,
)

def _extract_html_classes(html: str) -> Set[str]:
    classes: Set[str] = set()
    for pattern in (_HTML_CLASS_RE, _HTML_CLASS_RE_SINGLE):
        for m in pattern.finditer(html):
            for token in m.group(1).split():
                token = token.strip()
                if token and _VALID_CLASS_TOKEN.match(token):
                    classes.add(token)
    return classes


def _strip_embedded_scripts_and_styles(html: str) -> str:
    html = _HTML_SCRIPT_BLOCK.sub("", html)
    html = _HTML_STYLE_BLOCK.sub("", html)
    return html

def _project_uses_tailwind(project_root: str, html_files: List[str], css_files: List[str]) -> bool:
    for rel in css_files[:5]:
        try:
            with open(os.path.join(project_root, rel), "r", encoding="utf-8", errors="replace") as f:
                if "@tailwind" in f.read(8000): return True
        except OSError: continue
    for rel in html_files[:5]:
        try:
            with open(os.path.join(project_root, rel), "r", encoding="utf-8", errors="replace") as f:
                if _TAILWIND_MARKERS.search(f.read(8000)): return True
        except OSError: continue
    return False


def _extract_html_ids(html: str) -> Set[str]:
    ids: Set[str] = set()
    for pattern in (_HTML_ID_RE, _HTML_ID_RE_SINGLE):
        for m in pattern.finditer(html):
            for token in m.group(1).split():
                token = token.strip()
                if token:
                    ids.add(token)
    return ids


def _extract_html_references(html: str) -> List[str]:
    refs: List[str] = []
    for m in _HTML_SRC_RE.finditer(html):
        refs.append(m.group(1))
    for m in _HTML_HREF_RE.finditer(html):
        refs.append(m.group(1))
    return refs


def _extract_data_tab_values(html: str) -> Set[str]:
    """
    Return the set of `data-tab="X"` values from HTML.

    These identify interactive tabs. Each such value X must have a
    matching `<section id="view-X">` in HTML and at least one reference
    to `#view-X` in some JS file (typically `$('#view-X')` in an
    `el.views` map).
    """
    return {m.group(2).strip() for m in _HTML_DATA_TAB.finditer(html) if m.group(2).strip()}


def _extract_inline_scripts(html: str) -> List[str]:
    return [m.group(1) for m in _HTML_INLINE_SCRIPT_RE.finditer(html)]


# ---------------------------------------------------------------------------
# CSS parsing
# ---------------------------------------------------------------------------
_CSS_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_CSS_CLASS_SELECTOR_RE = re.compile(r"\.([a-zA-Z_][a-zA-Z0-9_-]*)")
_CSS_VAR_DEF_RE = re.compile(r"(--[a-zA-Z0-9_-]+)\s*:")
_CSS_VAR_USE_RE = re.compile(r"var\(\s*(--[a-zA-Z0-9_-]+)")
_CSS_URL_RE = re.compile(r"url\(\s*['\"]?([^)'\"]+)['\"]?\s*\)")
_CSS_SKIP_PRAGMA = re.compile(r"/\*\s*vegex:\s*skip-coherence-check\s*\*/")


def _extract_css(styles: str) -> Tuple[Set[str], Set[str], Set[str], List[str]]:
    """Returns (class selectors, vars defined, vars used, url refs)."""
    # If the file opts out of coherence checking, return empty
    if _CSS_SKIP_PRAGMA.search(styles):
        logger.debug("CSS file opted out via skip-coherence-check pragma")
        return set(), set(), set(), []

    body = _CSS_COMMENT_RE.sub("", styles)

    classes = set(_CSS_CLASS_SELECTOR_RE.findall(body))
    vars_defined = set(_CSS_VAR_DEF_RE.findall(body))
    vars_used = set(_CSS_VAR_USE_RE.findall(body))
    url_refs = [
        u for u in _CSS_URL_RE.findall(body)
        if not u.startswith(("data:", "http://", "https://", "//"))
    ]

    return classes, vars_defined, vars_used, url_refs


# ---------------------------------------------------------------------------
# JavaScript parsing — the key addition
# ---------------------------------------------------------------------------
_JS_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)

# Literal-string selector calls
_JS_QUERY_SELECTOR = re.compile(
    r"""(?:querySelector(?:All)?|matches|closest)\s*\(\s*(['"])(.*?)\1""",
    re.DOTALL,
)
_JS_GET_ELEMENT_BY_ID = re.compile(
    r"""getElementById\s*\(\s*(['"])(.*?)\1""",
    re.DOTALL,
)
_JS_INLINE_HTML_CLASS = re.compile(r"""class\s*=\s*(["'])([^"']*?)\1""", re.IGNORECASE)
_JS_GET_ELEMENTS_BY_CLASS = re.compile(
    r"""getElementsByClassName\s*\(\s*([^)]*?)\)""",
    re.DOTALL,
)
_JS_CLASS_LIST_CALL = re.compile(
    r"""classList\.(?:add|remove|toggle|contains)\s*\(([^)]*?)\)""",
    re.DOTALL,
)
_JS_CLASSNAME_ASSIGN = re.compile(
    r"""\.className\s*=\s*(['"])([^'"]*?)\1""",
    re.DOTALL,
)
_JS_SET_ATTRIBUTE_CLASS = re.compile(
    r"""setAttribute\s*\(\s*['"]class['"]\s*,\s*(['"])([^'"]*?)\1""",
    re.DOTALL,
)
_JS_JQUERY_SELECTOR = re.compile(
    r"""\$\(\s*(['"])(.*?)\1\s*\)""",
    re.DOTALL,
)

# Any reference to a view panel id in JS. Catches $(), getElementById,
# querySelector, querySelectorAll, and string literals used as selectors.
_JS_VIEW_ID_REF = re.compile(
    r"""#view-([a-zA-Z0-9_-]+)""",
)

# Split a selector string into class tokens (`.foo`) and ID tokens (`#bar`)
_SELECTOR_CLASS_TOKEN = re.compile(r"\.([a-zA-Z_][a-zA-Z0-9_-]*)")
_SELECTOR_ID_TOKEN = re.compile(r"#([a-zA-Z_][a-zA-Z0-9_-]*)")

# Extract all quoted string literals from a parenthesized argument list
_JS_STRING_LITERAL = re.compile(r"""(['"])([^'"]*?)\1""")


def _extract_js_findings(js: str) -> Dict[str, Set[str]]:
    """
    Extract selector targets from JS source code.

    Returns:
        dict with keys:
            'classes_queried': classes appearing in querySelector / matches / closest
            'ids_queried':     IDs appearing in getElementById / querySelector('#...')
            'classes_added':   classes added via classList.add / className =
    """
    # Strip block comments to avoid parsing commented-out code.
    # We deliberately don't strip line comments because URLs inside strings
    # contain `//` which would break naive line-comment removal.
    body = _JS_BLOCK_COMMENT_RE.sub("", js)

    classes_queried: Set[str] = set()
    ids_queried: Set[str] = set()
    classes_added: Set[str] = set()

    # 1. querySelector / querySelectorAll / matches / closest
    for m in _JS_QUERY_SELECTOR.finditer(body):
        selector = m.group(2)
        classes_queried.update(_SELECTOR_CLASS_TOKEN.findall(selector))
        ids_queried.update(_SELECTOR_ID_TOKEN.findall(selector))

    # 2. getElementById
    for m in _JS_GET_ELEMENT_BY_ID.finditer(body):
        ids_queried.add(m.group(2).strip())

    # 3. getElementsByClassName('a b c')
    for m in _JS_GET_ELEMENTS_BY_CLASS.finditer(body):
        for lit in _JS_STRING_LITERAL.finditer(m.group(1)):
            for cls in lit.group(2).split():
                cls = cls.strip()
                if cls:
                    classes_queried.add(cls)

    # 4. classList.add('a', 'b', 'c')
    for m in _JS_CLASS_LIST_CALL.finditer(body):
        for lit in _JS_STRING_LITERAL.finditer(m.group(1)):
            for cls in lit.group(2).split():
                cls = cls.strip()
                if cls:
                    classes_added.add(cls)

    # 5. element.className = 'a b c'
    for m in _JS_CLASSNAME_ASSIGN.finditer(body):
        for cls in m.group(2).split():
            cls = cls.strip()
            if cls:
                classes_added.add(cls)

    # 6. setAttribute('class', 'a b c')
    for m in _JS_SET_ATTRIBUTE_CLASS.finditer(body):
        for cls in m.group(2).split():
            cls = cls.strip()
            if cls:
                classes_added.add(cls)

    # 7. jQuery $(selector)
    for m in _JS_JQUERY_SELECTOR.finditer(body):
        selector = m.group(2)
        classes_queried.update(_SELECTOR_CLASS_TOKEN.findall(selector))
        ids_queried.update(_SELECTOR_ID_TOKEN.findall(selector))
    # 8. class="..." inside JS string literals (innerHTML, templates).
    for m in _JS_INLINE_HTML_CLASS.finditer(body):
        for cls in m.group(2).split():
            cls = cls.strip()
            if cls and _VALID_CLASS_TOKEN.match(cls):
                classes_added.add(cls)

    return {
        "classes_queried": classes_queried,
        "ids_queried": ids_queried,
        "classes_added": classes_added,
    }


# ---------------------------------------------------------------------------
# File resolution
# ---------------------------------------------------------------------------
_EXTERNAL_REF_PREFIXES = ("http://", "https://", "//", "data:", "mailto:", "tel:", "#")


def _is_external(ref: str) -> bool:
    if not ref:
        return True
    if ref.startswith(_EXTERNAL_REF_PREFIXES):
        return True
    if ref.startswith("javascript:"):
        return True
    return False


def _resolve_reference(ref: str, project_root: str, source_file: str) -> bool:
    if _is_external(ref):
        return True

    clean = ref.split("#", 1)[0].split("?", 1)[0]
    if not clean:
        return True

    source_dir = os.path.dirname(os.path.join(project_root, source_file))
    candidate = os.path.normpath(os.path.join(source_dir, clean))
    return os.path.exists(candidate) or os.path.exists(
        os.path.join(project_root, clean)
    )


# ---------------------------------------------------------------------------
# Tab-routing contract check
# ---------------------------------------------------------------------------
def _check_tab_routing_contract(
    data_tabs: Set[str],
    html_ids: Set[str],
    js_view_refs: Set[str],
    report: "CoherenceReport",
) -> None:
    """
    Verify that every `data-tab="X"` in HTML is wired up correctly:

      1. There must be an element with `id="view-X"` in HTML.
      2. Some JS file must reference `#view-X` (so the tab is switchable).

    Both checks are deterministic. They catch the specific class of bug
    where a tab button exists in the nav but the corresponding view
    section is missing (or the JS map that switches views doesn't
    include it).

    `data_tabs`       : values from data-tab="..."
    `html_ids`        : values from id="..." (includes view-* if present)
    `js_view_refs`    : suffix values from #view-... references in JS
    """
    if not data_tabs:
        return

    missing_sections: List[str] = []
    unwired_tabs: List[str] = []

    for tab in sorted(data_tabs):
        expected_id = f"view-{tab}"
        if expected_id not in html_ids:
            missing_sections.append(tab)
        if tab not in js_view_refs:
            unwired_tabs.append(tab)

    if missing_sections:
        report.issues.append(CoherenceIssue(
            kind="tab_missing_section",
            detail=(
                f"{len(missing_sections)} tab button(s) have no matching "
                f"<section id='view-X'> in HTML: "
                f"{', '.join(f'data-tab={t!r} → expected id=view-{t}' for t in missing_sections[:6])}"
                + (" ..." if len(missing_sections) > 6 else "")
            ),
            severity="error",
        ))

    if unwired_tabs:
        report.issues.append(CoherenceIssue(
            kind="tab_unwired_in_js",
            detail=(
                f"{len(unwired_tabs)} tab button(s) reference '#view-X' "
                f"nowhere in any JS file (clicking them will do nothing): "
                f"{', '.join(f'data-tab={t!r} → expected #view-{t} in JS' for t in unwired_tabs[:6])}"
                + (" ..." if len(unwired_tabs) > 6 else "")
            ),
            severity="error",
        ))


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def check_coherence(
    project_root: str,
    files: Optional[Iterable[str]] = None,
) -> CoherenceReport:
    """
    Inspect the project's HTML/CSS/JS files and report cross-file issues.
    Never raises.
    """
    report = CoherenceReport()
    _all_data_tabs: Set[str] = set()
    _js_view_refs: Set[str] = set()

    try:
        html_files, css_files, js_files = _discover_files(project_root, files)
    except Exception as exc:
        report.skipped_reason = f"file discovery failed: {exc}"
        return report

    if not html_files and not css_files and not js_files:
        report.skipped_reason = "no HTML, CSS, or JS files found"
        return report

    _tailwind_mode = _project_uses_tailwind(project_root, html_files, css_files)
    if _tailwind_mode:
        report.notes.append(
            "Tailwind detected — skipping unstyled_html_classes and orphan_css_selectors."
        )

    # ---- Parse HTML ----
    for rel_path in html_files:
        abs_path = os.path.join(project_root, rel_path)
        try:
            with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                html = f.read()
        except OSError as exc:
            logger.debug("Could not read %s: %s", rel_path, exc)
            continue

        report.files_checked.append(rel_path)
        html_for_static = _strip_embedded_scripts_and_styles(html)
        report.html_classes |= _extract_html_classes(html_for_static)
        report.html_ids |= _extract_html_ids(html_for_static)
        report.html_ids |= _extract_html_ids(html)
        # Tab buttons: register their data-tab values for the coherence check.
        _all_data_tabs.update(_extract_data_tab_values(html))

        for ref in _extract_html_references(html):
            if not _resolve_reference(ref, project_root, rel_path):
                report.issues.append(CoherenceIssue(
                    kind="missing_reference",
                    detail=f"'{ref}' does not exist on disk",
                    severity="error",
                    source_file=rel_path,
                ))

        # Inline <script> blocks are parsed as JS
        for inline_js in _extract_inline_scripts(html):
            if not inline_js.strip():
                continue
            findings = _extract_js_findings(inline_js)
            report.js_classes_queried |= findings["classes_queried"]
            report.js_ids_queried |= findings["ids_queried"]
            report.js_classes_added |= findings["classes_added"]

    # ---- Parse CSS ----
    css_vars_used_in_css: Set[str] = set()
    for rel_path in css_files:
        abs_path = os.path.join(project_root, rel_path)
        try:
            with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                styles = f.read()
        except OSError as exc:
            logger.debug("Could not read %s: %s", rel_path, exc)
            continue

        report.files_checked.append(rel_path)
        classes, vars_def, vars_used, urls = _extract_css(styles)
        report.css_selectors |= classes
        report.css_vars_defined |= vars_def
        css_vars_used_in_css |= vars_used

        for url in urls:
            if not _resolve_reference(url, project_root, rel_path):
                report.issues.append(CoherenceIssue(
                    kind="missing_reference",
                    detail=f"url('{url}') in CSS does not exist on disk",
                    severity="warning",
                    source_file=rel_path,
                ))

    report.css_vars_used = css_vars_used_in_css

    # ---- Parse JS files ----
    for rel_path in js_files:
        abs_path = os.path.join(project_root, rel_path)
        try:
            with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                js = f.read()
        except OSError as exc:
            logger.debug("Could not read %s: %s", rel_path, exc)
            continue

        report.files_checked.append(rel_path)
        # Collect view-id references so we can verify tab wiring later.
        for m in _JS_VIEW_ID_REF.finditer(js):
            _js_view_refs.add(m.group(1))
        findings = _extract_js_findings(js)
        report.js_classes_queried |= findings["classes_queried"]
        report.js_ids_queried |= findings["ids_queried"]
        report.js_classes_added |= findings["classes_added"]

    # ---- Set-level coherence checks ----

    # 1 & 2. Unstyled HTML classes / orphan CSS selectors.
    #    Skipped under Tailwind: utility classes are not defined as
    #    traditional CSS rules and would produce massive false positives.
    if not _tailwind_mode:
        # 1. Unstyled HTML classes.
        #    Every class on disk in HTML must have a CSS rule.
        unstyled = report.html_classes - report.css_selectors - _SAFE_CSS_SELECTORS
        if unstyled:
            sample = sorted(unstyled)
            report.issues.append(CoherenceIssue(
                kind="unstyled_html_classes",
                detail=(
                    f"{len(unstyled)} class(es) in HTML have no matching CSS rule: "
                    f"{', '.join('.' + c for c in sample[:12])}"
                    + (" ..." if len(sample) > 12 else "")
                ),
                severity="error",
            ))

        # 2. Orphan CSS selectors.
        #    A CSS rule whose class exists neither in HTML nor is added by JS
        #    will never match any element. ERROR.
        #    Accounting for js_classes_added prevents false positives on state
        #    classes like .is-active that JS toggles at runtime.
        known_classes = (
            report.html_classes
            | report.js_classes_added
        )
        orphan = report.css_selectors - known_classes - _SAFE_CSS_SELECTORS
        if orphan:
            sample = sorted(orphan)
            report.issues.append(CoherenceIssue(
                kind="orphan_css_selectors",
                detail=(
                    f"{len(orphan)} CSS rule(s) reference classes not present in "
                    f"HTML and not added by any JS: "
                    f"{', '.join('.' + c for c in sample[:12])}"
                    + (" ..." if len(sample) > 12 else "")
                ),
                severity="error",
            ))

    # 3. Undefined CSS variables.
    undefined_vars = css_vars_used_in_css - report.css_vars_defined
    if undefined_vars:
        sample = sorted(undefined_vars)
        report.issues.append(CoherenceIssue(
            kind="undefined_css_variables",
            detail=(
                f"{len(undefined_vars)} CSS variable(s) used but never defined: "
                f"{', '.join(sample[:10])}"
                + (" ..." if len(sample) > 10 else "")
            ),
            severity="warning",
        ))

    # 4. JS queries classes that don't exist anywhere.
    #    This is THE check that catches the exact "JS silent failure" bug:
    #    the agent writes JS expecting .task-options but HTML has .task-answers.
    #    The class isn't in HTML and isn't dynamically added by JS, so any
    #    document.querySelector('.task-options') will return null.
    missing_js_classes = (
        report.js_classes_queried
        - report.html_classes
        - report.js_classes_added
    )
    if missing_js_classes:
        sample = sorted(missing_js_classes)
        report.issues.append(CoherenceIssue(
            kind="js_query_missing_class",
            detail=(
                f"JS queries {len(missing_js_classes)} class(es) that exist "
                f"neither in HTML nor as JS-added classes. These selectors "
                f"will return null at runtime: "
                f"{', '.join('.' + c for c in sample[:12])}"
                + (" ..." if len(sample) > 12 else "")
            ),
            severity="error",
        ))

    # 5. JS queries IDs that don't exist in HTML.
    missing_js_ids = report.js_ids_queried - report.html_ids
    if missing_js_ids:
        sample = sorted(missing_js_ids)
        report.issues.append(CoherenceIssue(
            kind="js_query_missing_id",
            detail=(
                f"JS queries {len(missing_js_ids)} ID(s) that do not exist in "
                f"HTML. getElementById will return null: "
                f"{', '.join('#' + i for i in sample[:12])}"
                + (" ..." if len(sample) > 12 else "")
            ),
            severity="error",
        ))

    # 6. JS adds classes with no CSS rule.
    #    These classes will be added to elements but will not change their
    #    appearance. Often indicates a missing state styling rule
    #    (.is-open, .has-error, etc.).
    unstyled_js_added = (
        report.js_classes_added
        - report.css_selectors
        - _SAFE_CSS_SELECTORS
    )
    if unstyled_js_added:
        sample = sorted(unstyled_js_added)
        report.issues.append(CoherenceIssue(
            kind="js_added_class_unstyled",
            detail=(
                f"JS adds {len(unstyled_js_added)} class(es) that have no CSS "
                f"rule (state changes may be invisible): "
                f"{', '.join('.' + c for c in sample[:12])}"
                + (" ..." if len(sample) > 12 else "")
            ),
            severity="warning",
        ))

    # ---- Tab routing contract (HTML data-tab ↔ JS #view-X wiring) ----
    try:
        _check_tab_routing_contract(
            data_tabs=_all_data_tabs,
            html_ids=report.html_ids,
            js_view_refs=_js_view_refs,
            report=report,
        )
    except Exception as exc:
        logger.debug("Tab-routing contract check failed: %s", exc)

    return report


def _discover_files(
    project_root: str,
    files: Optional[Iterable[str]],
) -> Tuple[List[str], List[str], List[str]]:
    """Return (html_relative_paths, css_relative_paths, js_relative_paths)."""
    if files is not None:
        html_files = [f for f in files if f.lower().endswith((".html", ".htm"))]
        css_files = [f for f in files if f.lower().endswith((".css", ".scss"))]
        js_files = [f for f in files if f.lower().endswith((".js", ".mjs"))]
        return html_files, css_files, js_files

    ignored_dirs = {
        ".git", ".vegex", "node_modules", "__pycache__", ".venv", "venv",
        "dist", "build", "iteration_logs", ".pytest_cache", ".mypy_cache",
    }

    html_files: List[str] = []
    css_files: List[str] = []
    js_files: List[str] = []
    root = Path(project_root).resolve()

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames
            if d not in ignored_dirs and not d.startswith(".")
        ]
        for fname in filenames:
            lower = fname.lower()
            rel = os.path.relpath(os.path.join(dirpath, fname), root).replace("\\", "/")
            if lower.endswith((".html", ".htm")):
                html_files.append(rel)
            elif lower.endswith((".css", ".scss")):
                css_files.append(rel)
            elif lower.endswith((".js", ".mjs")):
                js_files.append(rel)

    return sorted(html_files), sorted(css_files), sorted(js_files)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def render_report(report: CoherenceReport, max_lines: int = 20) -> str:
    """
    Compact text representation for injection into the reviewer prompt.
    """
    if report.skipped_reason:
        return f"[coherence] skipped: {report.skipped_reason}"

    if not report.issues:
        if not report.files_checked:
            return "[coherence] nothing to check"
        return (
            f"[coherence] OK — "
            f"{len(report.html_classes)} HTML classes, "
            f"{len(report.css_selectors)} CSS selectors, "
            f"{len(report.js_classes_queried)} JS queried classes, "
            f"all coherent"
        )

    lines = ["[coherence report]"]
    lines.append(
        f"Files checked: {', '.join(report.files_checked[:6])}"
        + (f" (+{len(report.files_checked) - 6} more)"
           if len(report.files_checked) > 6 else "")
    )
    lines.append(
        f"Totals: {len(report.html_classes)} HTML classes, "
        f"{len(report.html_ids)} HTML IDs, "
        f"{len(report.css_selectors)} CSS selectors, "
        f"{len(report.js_classes_queried)} JS queried classes, "
        f"{len(report.js_classes_added)} JS added classes"
    )

    errors = report.errors
    warnings = report.warnings

    if errors:
        lines.append(f"ERRORS ({len(errors)}):")
        for issue in errors[: max(1, max_lines - len(lines) - 2)]:
            lines.append(f"  • {issue.as_line()}")

    if warnings:
        lines.append(f"WARNINGS ({len(warnings)}):")
        remaining = max(1, max_lines - len(lines))
        for issue in warnings[:remaining]:
            lines.append(f"  • {issue.as_line()}")

    return "\n".join(lines)


__all__ = [
    "CoherenceIssue",
    "CoherenceReport",
    "check_coherence",
    "render_report",
]