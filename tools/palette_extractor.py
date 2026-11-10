"""
tools/palette_extractor.py
from plugin: webdev
--------------------------
Extract a DifferiX-compatible palette from a URL or a local HTML file.

Given a web page, this tool:
  1. Fetches the HTML (SSRF-guarded) and any linked stylesheets.
  2. Extracts every color, font-family, radius, and transition it finds.
  3. Clusters colors by role: backgrounds, accents, text, borders.
  4. Emits a `palettes/<name>.css` file that conforms to the DifferiX
     palette contract (see skills/webdev.md).

The output is not a copy of the source CSS. It is a new file with our
variable names, filled with values derived from the source's visual
identity. Colors and fonts are not copyrightable; the file we write
contains none of the source's code.

Public API:
    extract_palette(source, output_dir="palettes", name=None,
                    dry_run=False) -> PaletteResult

CLI:
    python tools/palette_extractor.py <url-or-file> [-o DIR] [--name NAME] [--dry-run]

Never raises on network failure — returns a PaletteResult with
`success=False` and an `error` string. The caller decides what to do.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, asdict
from html.parser import HTMLParser
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lazy imports for optional / internal dependencies
# ---------------------------------------------------------------------------
def _import_web_search():
    """Import the SSRF guards from web_search.py (same project)."""
    try:
        from tools import web_search as _ws
        return _ws
    except ImportError:
        try:
            import web_search as _ws  # type: ignore
            return _ws
        except ImportError:
            return None


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# A small named-color table. Only the common ones — the full CSS list is
# 148 entries and provides negligible additional value.
_NAMED_COLORS: Dict[str, Tuple[int, int, int]] = {
    "black": (0, 0, 0), "white": (255, 255, 255),
    "red": (255, 0, 0), "green": (0, 128, 0), "blue": (0, 0, 255),
    "yellow": (255, 255, 0), "cyan": (0, 255, 255), "magenta": (255, 0, 255),
    "gray": (128, 128, 128), "grey": (128, 128, 128),
    "silver": (192, 192, 192), "maroon": (128, 0, 0),
    "olive": (128, 128, 0), "lime": (0, 255, 0),
    "aqua": (0, 255, 255), "teal": (0, 128, 128),
    "navy": (0, 0, 128), "fuchsia": (255, 0, 255),
    "purple": (128, 0, 128), "orange": (255, 165, 0),
    "pink": (255, 192, 203), "brown": (165, 42, 42),
    "gold": (255, 215, 0), "indigo": (75, 0, 130),
    "crimson": (220, 20, 60), "coral": (255, 127, 80),
    "salmon": (250, 128, 114), "tomato": (255, 99, 71),
    "khaki": (240, 230, 140), "lavender": (230, 230, 250),
    "beige": (245, 245, 220), "ivory": (255, 255, 240),
    "transparent": (0, 0, 0),
    "currentcolor": (0, 0, 0), "inherit": (0, 0, 0),
}

# Colors that should never be treated as accent candidates, even if they
# appear frequently. These are almost always utility or reset colors.
_IGNORE_AS_ACCENT = {
    (0, 0, 0), (255, 255, 255),
}

# Frequency threshold — colors appearing fewer times than this are
# treated as one-off and excluded from the main clustering. Set to 2
# so that a color must appear in at least two rules to count.
_MIN_OCCURRENCES = 2

# CSS regexes — deliberately conservative. We would rather miss a color
# than misclassify a selector as a color.
_RE_HEX = re.compile(
    r"#([0-9a-fA-F]{3,8})\b"
)
_RE_RGB = re.compile(
    r"rgba?\(\s*"
    r"([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)"
    r"(?:\s*,\s*([\d.]+))?\s*\)",
    re.IGNORECASE,
)
_RE_HSL = re.compile(
    r"hsla?\(\s*"
    r"([\d.]+)(?:deg)?\s*,\s*([\d.]+)%\s*,\s*([\d.]+)%"
    r"(?:\s*,\s*([\d.]+))?\s*\)",
    re.IGNORECASE,
)
_RE_FONT_FAMILY = re.compile(
    r"font-family\s*:\s*([^;}\n]+)",
    re.IGNORECASE,
)
_RE_RULE = re.compile(
    r"([^{}]+)\{([^{}]*)\}",
    re.DOTALL,
)
_RE_CUSTOM_PROP = re.compile(
    r"(--[a-zA-Z][\w-]*)\s*:\s*([^;}\n]+)",
)
_RE_STYLE_BLOCK = re.compile(
    r"<style[^>]*>(.*?)</style>",
    re.DOTALL | re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------
@dataclass
class PaletteResult:
    success: bool
    palette_id: str = ""
    output_path: str = ""
    css_content: str = ""
    colors_detected: int = 0
    fonts_detected: int = 0
    is_dark_mode: bool = False
    error: str = ""
    source: str = ""

    def as_dict(self) -> Dict:
        return asdict(self)


class PaletteExtractionError(Exception):
    """Raised for unrecoverable errors during extraction."""


# ---------------------------------------------------------------------------
# Color utilities
# ---------------------------------------------------------------------------
def parse_color(s: str) -> Optional[Tuple[int, int, int]]:
    """
    Parse a color string into an (R, G, B) tuple. Returns None on failure.
    Accepts #rgb, #rrggbb, #rrggbbaa, rgb(), rgba(), hsl(), hsla(), and
    the subset of CSS named colors in _NAMED_COLORS.
    """
    if not s:
        return None
    s = s.strip().lower()

    # Named
    if s in _NAMED_COLORS:
        return _NAMED_COLORS[s]

    # Hex
    if s.startswith("#"):
        h = s[1:]
        try:
            if len(h) == 3:
                return tuple(int(c * 2, 16) for c in h)  # type: ignore
            if len(h) == 6:
                return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
            if len(h) == 8:
                # ignore alpha
                return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
        except ValueError:
            return None
        return None

    # rgb() / rgba()
    m = _RE_RGB.fullmatch(s)
    if m:
        try:
            return (
                int(float(m.group(1))),
                int(float(m.group(2))),
                int(float(m.group(3))),
            )
        except (ValueError, TypeError):
            return None

    # hsl() / hsla()
    m = _RE_HSL.fullmatch(s)
    if m:
        try:
            h = float(m.group(1))
            s_pct = float(m.group(2)) / 100.0
            l_pct = float(m.group(3)) / 100.0
            return hsl_to_rgb(h, s_pct, l_pct)
        except (ValueError, TypeError):
            return None

    return None


def rgb_to_hsl(rgb: Tuple[int, int, int]) -> Tuple[float, float, float]:
    """Return (H in [0,360), S in [0,1], L in [0,1])."""
    r, g, b = [v / 255.0 for v in rgb]
    mx = max(r, g, b)
    mn = min(r, g, b)
    l = (mx + mn) / 2.0
    if mx == mn:
        return (0.0, 0.0, l)
    d = mx - mn
    s = d / (2.0 - mx - mn) if l > 0.5 else d / (mx + mn)
    if mx == r:
        h = ((g - b) / d) % 6.0
    elif mx == g:
        h = (b - r) / d + 2.0
    else:
        h = (r - g) / d + 4.0
    return (h * 60.0, s, l)


def hsl_to_rgb(h: float, s: float, l: float) -> Tuple[int, int, int]:
    h = (h % 360.0) / 360.0
    s = max(0.0, min(1.0, s))
    l = max(0.0, min(1.0, l))
    if s == 0.0:
        v = int(round(l * 255.0))
        return (v, v, v)
    q = l * (1 + s) if l < 0.5 else l + s - l * s
    p = 2 * l - q

    def hue(t: float) -> float:
        t = t % 1.0
        if t < 1 / 6:
            return p + (q - p) * 6 * t
        if t < 1 / 2:
            return q
        if t < 2 / 3:
            return p + (q - p) * (2 / 3 - t) * 6
        return p

    r = int(round(hue(h + 1 / 3) * 255))
    g = int(round(hue(h) * 255))
    b = int(round(hue(h - 1 / 3) * 255))
    return (max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b)))


def rgb_to_hex(rgb: Tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def rgba_string(rgb: Tuple[int, int, int], alpha: float) -> str:
    a = max(0.0, min(1.0, alpha))
    return f"rgba({rgb[0]}, {rgb[1]}, {rgb[2]}, {a:.2f})"


def luminance(rgb: Tuple[int, int, int]) -> float:
    """Relative luminance approximation in [0, 1]."""
    r, g, b = rgb
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255.0


def _blend_toward(
    color: Tuple[int, int, int],
    target: Tuple[int, int, int],
    t: float,
) -> Tuple[int, int, int]:
    """
    Blend `color` toward `target` by factor `t` (0..1).
    t=0 returns color, t=1 returns target.
    """
    t = max(0.0, min(1.0, t))
    r1, g1, b1 = color
    r2, g2, b2 = target
    return (
        int(round(r1 * (1 - t) + r2 * t)),
        int(round(g1 * (1 - t) + g2 * t)),
        int(round(b1 * (1 - t) + b2 * t)),
    )


def adjust_lightness(rgb: Tuple[int, int, int], delta: float) -> Tuple[int, int, int]:
    """Shift the L channel of an RGB color by delta (positive = lighter)."""
    h, s, l = rgb_to_hsl(rgb)
    return hsl_to_rgb(h, s, max(0.0, min(1.0, l + delta)))


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------
def _is_remote(source: str) -> bool:
    return source.startswith("http://") or source.startswith("https://")


def _fetch_url(url: str, timeout: float = 15.0) -> str:
    """SSRF-guarded fetch. Returns the raw HTML."""
    ws = _import_web_search()
    if ws is None:
        raise PaletteExtractionError(
            "web_search module not importable — cannot fetch remote URLs"
        )

    safe, reason = ws._is_url_safe(url)  # type: ignore[attr-defined]
    if not safe:
        raise PaletteExtractionError(f"Blocked unsafe URL: {reason}")

    opener = ws._build_safe_opener()  # type: ignore[attr-defined]
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "DifferiX/0.4 palette-extractor",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    try:
        with opener.open(req, timeout=timeout) as response:
            raw = ws._read_capped(response)  # type: ignore[attr-defined]
    except Exception as exc:
        raise PaletteExtractionError(f"Failed to fetch {url}: {exc}") from exc

    return raw.decode("utf-8", errors="replace")


def _fetch_css(url: str, timeout: float = 10.0) -> str:
    """Fetch a linked stylesheet. Returns "" on failure (non-fatal)."""
    ws = _import_web_search()
    if ws is None:
        return ""
    safe, _ = ws._is_url_safe(url)  # type: ignore[attr-defined]
    if not safe:
        return ""
    opener = ws._build_safe_opener()  # type: ignore[attr-defined]
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "DifferiX/0.4 palette-extractor"},
    )
    try:
        with opener.open(req, timeout=timeout) as response:
            raw = ws._read_capped(response, max_bytes=2_000_000)  # type: ignore[attr-defined]
        return raw.decode("utf-8", errors="replace")
    except Exception as exc:
        logger.debug("Skipped stylesheet %s: %s", url, exc)
        return ""


def _read_local(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError as exc:
        raise PaletteExtractionError(f"Failed to read {path}: {exc}") from exc


# ---------------------------------------------------------------------------
# HTML parsing
# ---------------------------------------------------------------------------
class _StylesheetLinkCollector(HTMLParser):
    """Collects <link rel="stylesheet"> hrefs and inline <style> bodies."""

    def __init__(self) -> None:
        super().__init__()
        self.link_hrefs: List[str] = []
        self.inline_styles: List[str] = []
        self._in_style = False
        self._style_buffer: List[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        tag_lower = tag.lower()
        attr_dict = {k.lower(): (v or "") for k, v in attrs}
        if tag_lower == "link":
            rel = attr_dict.get("rel", "").lower()
            href = attr_dict.get("href", "")
            if "stylesheet" in rel and href:
                self.link_hrefs.append(href)
        elif tag_lower == "style":
            self._in_style = True
            self._style_buffer = []

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "style" and self._in_style:
            self.inline_styles.append("".join(self._style_buffer))
            self._in_style = False
            self._style_buffer = []

    def handle_data(self, data: str) -> None:
        if self._in_style:
            self._style_buffer.append(data)


def _collect_css(html: str, base_url: Optional[str]) -> str:
    """
    Combine all CSS sources: inline <style> blocks and linked stylesheets.
    Returns a single concatenated CSS string.
    """
    collector = _StylesheetLinkCollector()
    try:
        collector.feed(html)
    except Exception as exc:
        logger.warning("HTML parsing partial: %s", exc)

    parts: List[str] = list(collector.inline_styles)

    # Also do a regex pass for <style> in case HTMLParser missed something
    parts.extend(_RE_STYLE_BLOCK.findall(html))

    # Fetch linked stylesheets
    for href in collector.link_hrefs:
        if not base_url:
            continue
        full = urllib.parse.urljoin(base_url, href)
        # Skip well-known font CDNs — they don't contain palette data
        if any(cdn in full for cdn in (
            "fonts.googleapis.com", "fonts.gstatic.com",
            "use.typekit.net", "cdn.jsdelivr.net",
        )):
            continue
        css = _fetch_css(full)
        if css:
            parts.append(css)

    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# CSS parsing
# ---------------------------------------------------------------------------
def _extract_color_occurrences(css: str) -> Dict[str, int]:
    """
    Return a mapping from normalized hex -> occurrence count.
    Parses hex, rgb(), rgba(), hsl(), hsla(), and named colors.
    """
    counts: Dict[str, int] = {}

    # Hex
    for m in _RE_HEX.finditer(css):
        raw = "#" + m.group(1).lower()
        rgb = parse_color(raw)
        if rgb:
            key = rgb_to_hex(rgb)
            counts[key] = counts.get(key, 0) + 1

    # rgb / rgba
    for m in _RE_RGB.finditer(css):
        raw = m.group(0)
        rgb = parse_color(raw)
        if rgb:
            key = rgb_to_hex(rgb)
            counts[key] = counts.get(key, 0) + 1

    # hsl / hsla
    for m in _RE_HSL.finditer(css):
        raw = m.group(0)
        rgb = parse_color(raw)
        if rgb:
            key = rgb_to_hex(rgb)
            counts[key] = counts.get(key, 0) + 1

    # Named colors — do a word-boundary search on the CSS
    for name, rgb in _NAMED_COLORS.items():
        pattern = re.compile(r"\b" + re.escape(name) + r"\b")
        n = len(pattern.findall(css))
        if n > 0 and name not in ("transparent", "currentcolor", "inherit"):
            key = rgb_to_hex(rgb)
            counts[key] = counts.get(key, 0) + n

    return counts


def _extract_custom_properties(css: str) -> Dict[str, str]:
    """Extract --var: value pairs from any rule."""
    out: Dict[str, str] = {}
    for m in _RE_CUSTOM_PROP.finditer(css):
        name = m.group(1).strip()
        value = m.group(2).strip()
        # Keep the first definition of a given variable
        if name not in out:
            out[name] = value
    return out


def _extract_fonts_by_context(
    css: str,
    custom_props: Optional[Dict[str, str]] = None,
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Return (heading_font, body_font, mono_font) inferred from the CSS.
    Resolves var() references against `custom_props` when provided.
    """
    if custom_props is None:
        custom_props = {}

    heading_font: Optional[str] = None
    body_font: Optional[str] = None
    mono_font: Optional[str] = None
    font_counter: Dict[str, int] = {}

    for m in _RE_RULE.finditer(css):
        selector = m.group(1).strip().lower()
        body = m.group(2)

        for fm in _RE_FONT_FAMILY.finditer(body):
            family = fm.group(1).strip().rstrip(";").strip()
            if not family:
                continue

            primary = _first_font_name(family)
            if not primary:
                # Maybe a bare var() reference — resolve it
                primary = _resolve_font_var(family, custom_props)
            if not primary:
                continue

            font_counter[primary] = font_counter.get(primary, 0) + 1

            if "mono" in primary.lower() or "code" in primary.lower():
                if mono_font is None:
                    mono_font = primary

            if heading_font is None:
                if any(s in selector for s in (
                    "h1", "h2", "h3", ".heading", ".title", ".headline",
                )):
                    heading_font = primary

    if font_counter:
        sorted_fonts = sorted(
            font_counter.items(),
            key=lambda kv: (-kv[1], kv[0].lower()),
        )
        for name, _ in sorted_fonts:
            if not _is_generic_font(name):
                body_font = name
                break
        if body_font is None:
            body_font = sorted_fonts[0][0]

    # Resolve any remaining var() references
    heading_font = _resolve_font_var(heading_font, custom_props)
    body_font = _resolve_font_var(body_font, custom_props)
    mono_font = _resolve_font_var(mono_font, custom_props)

    return (heading_font, body_font, mono_font)

_GENERIC_FONT_NAMES = {
    "serif", "sans-serif", "monospace", "cursive", "fantasy",
    "system-ui", "-apple-system", "blinkmacsystemfont",
    "inherit", "initial", "unset",
}


def _first_font_name(family_list: str) -> Optional[str]:
    """
    Given `"Inter", system-ui, sans-serif`, return `Inter`.
    Rejects CSS variable references — they are not font names.
    """
    parts = [p.strip().strip("'\"") for p in family_list.split(",")]
    for p in parts:
        p = p.strip()
        if not p:
            continue
        # Reject CSS variable references — the caller must resolve them
        if p.startswith("var(") or p.startswith("--"):
            continue
        if p.lower() in _GENERIC_FONT_NAMES:
            continue
        return p
    return parts[0] if parts and parts[0] else None

def _resolve_font_var(name: Optional[str], custom_props: Dict[str, str]) -> Optional[str]:
    """
    If `name` is a bare `var(--foo)` reference, resolve it against
    custom_props and return the first concrete font name inside.
    Returns None when resolution fails.
    """
    if not name:
        return None
    m = re.match(r"var\(\s*(--[\w-]+)\s*\)", name.strip())
    if not m:
        return name
    ref = m.group(1)
    resolved_value = custom_props.get(ref)
    if not resolved_value:
        return None
    # resolved_value is itself a font-family list — take the first concrete name
    return _first_font_name(resolved_value)


def _is_generic_font(name: str) -> bool:
    return name.lower() in _GENERIC_FONT_NAMES


# ---------------------------------------------------------------------------
# Clustering
# ---------------------------------------------------------------------------
def _classify_colors(
    color_counts: Dict[str, int],
) -> Tuple[bool, List[str], List[str], List[str], List[str]]:
    """
    Given a hex->count mapping, return:
      (is_dark_mode, backgrounds, accents, text_colors, neutrals)
    Each list is sorted by descending frequency.
    """
    # Filter: require minimum occurrence
    filtered = [
        (hex_str, parse_color(hex_str), count)
        for hex_str, count in color_counts.items()
    ]
    filtered = [(h, rgb, c) for h, rgb, c in filtered if rgb is not None and c >= _MIN_OCCURRENCES]

    if not filtered:
        # Fallback to all colors if nothing meets the threshold
        filtered = [
            (hex_str, parse_color(hex_str), count)
            for hex_str, count in color_counts.items()
        ]
        filtered = [(h, rgb, c) for h, rgb, c in filtered if rgb is not None]

    if not filtered:
        return (True, [], [], [], [])

    # Sort by count desc
    filtered.sort(key=lambda t: -t[2])

    # Determine mode from the top colors
    top = filtered[:5]
    avg_lum = sum(luminance(rgb) for _, rgb, _ in top) / len(top)
    is_dark = avg_lum < 0.5

    backgrounds: List[str] = []
    accents: List[str] = []
    text_colors: List[str] = []
    neutrals: List[str] = []

    for hex_str, rgb, _ in filtered:
        h, s, l = rgb_to_hsl(rgb)
        lum = luminance(rgb)

        is_chromatic = s > 0.20

        if is_chromatic:
            # Chromatic colors become accent candidates if reasonably saturated
            if s > 0.35 and rgb not in _IGNORE_AS_ACCENT:
                accents.append(hex_str)
            else:
                neutrals.append(hex_str)
        else:
            # Neutral: classify by luminance relative to the theme
            if is_dark:
                if lum < 0.12:
                    backgrounds.append(hex_str)
                elif lum > 0.75:
                    text_colors.append(hex_str)
                else:
                    neutrals.append(hex_str)
            else:
                if lum > 0.92:
                    backgrounds.append(hex_str)
                elif lum < 0.25:
                    text_colors.append(hex_str)
                else:
                    neutrals.append(hex_str)

    return (is_dark, backgrounds, accents, text_colors, neutrals)


# ---------------------------------------------------------------------------
# Palette construction
# ---------------------------------------------------------------------------
def _pick(lst: List[str], fallback: str, idx: int = 0) -> str:
    if len(lst) > idx:
        return lst[idx]
    if lst:
        return lst[-1]
    return fallback


def _match_custom_props_to_roles(props: Dict[str, str]) -> Dict[str, str]:
    """
    Try to guess the role of each custom property from its name, and map
    it to our token names. Returns {our_token: hex_value} for matches.
    Only includes matches where the value parses to a color.
    """
    role_to_hex: Dict[str, str] = {}

    def _value_to_hex(v: str) -> Optional[str]:
        rgb = parse_color(v.strip())
        return rgb_to_hex(rgb) if rgb else None

    patterns: List[Tuple[str, List[str]]] = [
        ("accent", ["primary", "brand", "accent", "main", "highlight", "link"]),
        ("bg-primary", ["bg", "background", "surface", "base", "body"]),
        ("text-main", ["text", "foreground", "fg", "content", "primary-text"]),
        ("border-subtle", ["border", "divider", "outline"]),
    ]

    for our_token, keywords in patterns:
        for name, value in props.items():
            lname = name.lower().lstrip("-")
            if any(kw in lname for kw in keywords):
                hex_val = _value_to_hex(value)
                if hex_val and our_token not in role_to_hex:
                    role_to_hex[our_token] = hex_val
                    break

    return role_to_hex


def build_palette(
    color_counts: Dict[str, int],
    fonts: Tuple[Optional[str], Optional[str], Optional[str]],
    custom_props: Dict[str, str],
) -> Tuple[Dict[str, str], bool]:
    """
    Build the palette dictionary. Returns (palette, is_dark_mode).
    """
    is_dark, bgs, accents, texts, neutrals = _classify_colors(color_counts)
    hinted = _match_custom_props_to_roles(custom_props)

    # ---- Backgrounds ----
    if is_dark:
        bg_default = "#0a0c10"
        bg_secondary_default = "#12161f"
        bg_tertiary_default = "#181d28"
        bg_card_default = "#141821"
        text_default = "#f3f4f6"
        text_muted_default = "#9ca3af"
        text_dim_default = "#6b7280"
        text_inverse_default = "#0a0c10"
    else:
        bg_default = "#ffffff"
        bg_secondary_default = "#f5f5f7"
        bg_tertiary_default = "#e8e8ec"
        bg_card_default = "#ffffff"
        text_default = "#111318"
        text_muted_default = "#4b5563"
        text_dim_default = "#6b7280"
        text_inverse_default = "#ffffff"

    bg_primary = hinted.get("bg-primary") or _pick(bgs, bg_default, 0)
    bg_secondary = _pick(bgs, bg_secondary_default, 1) if len(bgs) > 1 else bg_secondary_default
    bg_tertiary = _pick(bgs, bg_tertiary_default, 2) if len(bgs) > 2 else bg_tertiary_default

    # ---- Accent ----
    accent_hex = hinted.get("accent") or _pick(accents, "#f59e0b", 0)
    accent_rgb = parse_color(accent_hex) or (245, 158, 11)
    accent_light_rgb = adjust_lightness(accent_rgb, 0.12)
    accent_dark_rgb = adjust_lightness(accent_rgb, -0.12)

    # ---- Text ----
    text_main_hex = hinted.get("text-main") or _pick(texts, text_default, 0)
    text_main_rgb = parse_color(text_main_hex) or parse_color(text_default) or (243, 244, 246)

    bg_rgb = parse_color(bg_primary) or ((10, 12, 16) if is_dark else (255, 255, 255))

    # Derive muted and dim by blending the main text color toward the
    # background. This works in both light and dark mode and never
    # collapses to a single value, which adjust_lightness did when the
    # starting color was already at lightness 0 or 1.
    text_muted_rgb = _blend_toward(text_main_rgb, bg_rgb, 0.45)
    text_dim_rgb = _blend_toward(text_main_rgb, bg_rgb, 0.65)

    # ---- Borders ----
    border_subtle = hinted.get("border-subtle") or rgba_string(text_main_rgb, 0.08)
    border_medium = rgba_string(text_main_rgb, 0.16)
    border_accent = rgba_string(accent_rgb, 0.35)

    # ---- Fonts ----
    heading_font, body_font, mono_font = fonts
    heading_font = heading_font or body_font or "Space Grotesk"
    body_font = body_font or "Inter"
    mono_font = mono_font or "JetBrains Mono"

    # ---- Inverse text (used on top of the accent surface) ----
    accent_lum = luminance(accent_rgb)
    text_inverse_hex = "#0a0c10" if accent_lum > 0.5 else "#ffffff"

    palette: Dict[str, str] = {
        "--bg-primary":     bg_primary,
        "--bg-secondary":   bg_secondary,
        "--bg-tertiary":    bg_tertiary,
        "--bg-card":        bg_card_default,
        "--bg-glass":       rgba_string(bg_rgb, 0.72),

        "--accent":         rgb_to_hex(accent_rgb),
        "--accent-light":   rgb_to_hex(accent_light_rgb),
        "--accent-dark":    rgb_to_hex(accent_dark_rgb),
        "--accent-glow":    rgba_string(accent_rgb, 0.25),
        "--accent-soft":    rgba_string(accent_rgb, 0.10),

        "--text-main":      rgb_to_hex(text_main_rgb),
        "--text-muted":     rgb_to_hex(text_muted_rgb),
        "--text-dim":       rgb_to_hex(text_dim_rgb),
        "--text-inverse":   text_inverse_hex,

        "--border-subtle":  border_subtle,
        "--border-medium":  border_medium,
        "--border-accent":  border_accent,

        "--font-heading":   f"'{heading_font}', system-ui, sans-serif",
        "--font-body":      f"'{body_font}', system-ui, sans-serif",
        "--font-mono":      f"'{mono_font}', monospace",

        "--transition-fast":   "0.15s cubic-bezier(0.16, 1, 0.3, 1)",
        "--transition-smooth": "0.3s cubic-bezier(0.16, 1, 0.3, 1)",
    }
    return (palette, is_dark)


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
def _slug_from_source(source: str) -> str:
    if _is_remote(source):
        try:
            host = urllib.parse.urlparse(source).hostname or ""
        except Exception:
            host = source
        host = host.replace("www.", "")
        slug = re.sub(r"[^a-z0-9]+", "-", host.lower()).strip("-")
        return slug or "extracted"
    else:
        base = os.path.splitext(os.path.basename(source))[0]
        slug = re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")
        return slug or "extracted"


def _render_palette_css(
    palette: Dict[str, str],
    name: str,
    source: str,
    is_dark: bool,
) -> str:
    mood = "dark, high-contrast" if is_dark else "light, editorial"
    groups = [
        ("Surfaces",   ["--bg-primary", "--bg-secondary", "--bg-tertiary",
                        "--bg-card", "--bg-glass"]),
        ("Accent",     ["--accent", "--accent-light", "--accent-dark",
                        "--accent-glow", "--accent-soft"]),
        ("Text",       ["--text-main", "--text-muted", "--text-dim",
                        "--text-inverse"]),
        ("Borders",    ["--border-subtle", "--border-medium", "--border-accent"]),
        ("Typography", ["--font-heading", "--font-body", "--font-mono"]),
        ("Motion",     ["--transition-fast", "--transition-smooth"]),
    ]

    lines = [
        f"/* Palette: {name}",
        f"   Mood: {mood}",
        f"   Source: {source}",
        f"   Extracted: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}",
        f"",
        f"   This file was generated by tools/palette_extractor.py.",
        f"   The values are derived from the source's visual identity —",
        f"   they are not copied from the source's CSS. Edit freely.",
        f"*/",
        "",
        ":root {",
    ]

    for i, (label, keys) in enumerate(groups):
        if i > 0:
            lines.append("")
        lines.append(f"  /* {label} */")
        for key in keys:
            # Pad `key + ':'` to 22 chars so values align
            padded = f"{key}:".ljust(22)
            lines.append(f"  {padded} {palette[key]};")

    lines.append("}")
    lines.append("")
    return "\n".join(lines)

def extract_palette(
    source: str,
    output_dir: str = "palettes",
    name: Optional[str] = None,
    dry_run: bool = False,
) -> PaletteResult:
    """
    Extract a palette from a URL or local HTML file.

    Args:
        source: URL or path to local HTML file.
        output_dir: directory to write the palette file (created if missing).
        name: palette slug. Defaults to a slug derived from the source.
        dry_run: if True, don't write to disk — just return the CSS in the
            result.

    Returns:
        PaletteResult. Never raises for network or parse errors; check
        `.success` and `.error` fields.
    """
    t0 = time.time()
    result = PaletteResult(success=False, source=source)

    if not source:
        result.error = "Empty source"
        return result

    # ---- Fetch HTML ----
    try:
        if _is_remote(source):
            html = _fetch_url(source)
            base_url: Optional[str] = source
        else:
            html = _read_local(source)
            base_url = None
    except PaletteExtractionError as exc:
        result.error = str(exc)
        return result

    if not html or len(html) < 50:
        result.error = "Fetched content is too small to be a valid page"
        return result

    # ---- Collect CSS ----
    css = _collect_css(html, base_url)
    if not css:
        result.error = "No CSS found (no <style> blocks and no linked stylesheets)"
        return result

    # ---- Extract ----
    try:
        color_counts = _extract_color_occurrences(css)
        custom_props = _extract_custom_properties(css)
        fonts = _extract_fonts_by_context(css, custom_props=custom_props)
    except Exception as exc:
        logger.exception("Extraction failed")
        result.error = f"Extraction error: {exc}"
        return result

    if not color_counts:
        result.error = "No colors detected in CSS"
        return result

    # ---- Build palette ----
    palette, is_dark = build_palette(color_counts, fonts, custom_props)

    palette_name = name or _slug_from_source(source)
    css_content = _render_palette_css(palette, palette_name, source, is_dark)

    result.palette_id = palette_name
    result.css_content = css_content
    result.colors_detected = len(color_counts)
    result.fonts_detected = sum(1 for f in fonts if f)
    result.is_dark_mode = is_dark

    # ---- Write file ----
    if not dry_run:
        try:
            out_dir = Path(output_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / f"{palette_name}.css"
            out_path.write_text(css_content, encoding="utf-8")
            result.output_path = str(out_path)
        except OSError as exc:
            result.error = f"Failed to write palette file: {exc}"
            return result

    result.success = True
    logger.info(
        "Extracted palette %r from %s in %.2fs (%d colors, %d fonts, %s mode)",
        palette_name, source, time.time() - t0,
        result.colors_detected, result.fonts_detected,
        "dark" if is_dark else "light",
    )
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _cli() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Extract a DifferiX palette from a URL or local HTML file.",
    )
    parser.add_argument("source", help="URL or path to an HTML file")
    parser.add_argument(
        "-o", "--output-dir", default="palettes",
        help="Directory to write the palette file (default: palettes/)",
    )
    parser.add_argument(
        "--name", default=None,
        help="Palette slug (default: derived from the source domain)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print the palette to stdout without writing a file",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable debug logging",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    result = extract_palette(
        source=args.source,
        output_dir=args.output_dir,
        name=args.name,
        dry_run=args.dry_run,
    )

    if not result.success:
        print(f"ERROR: {result.error}", file=sys.stderr)
        return 1

    print(f"Palette: {result.palette_id}")
    print(f"Colors detected: {result.colors_detected}")
    print(f"Fonts detected: {result.fonts_detected}")
    print(f"Mode: {'dark' if result.is_dark_mode else 'light'}")

    if args.dry_run:
        print()
        print(result.css_content)
    else:
        print(f"Written to: {result.output_path}")

    return 0


if __name__ == "__main__":
    sys.exit(_cli())