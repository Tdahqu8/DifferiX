"""LLM-independent web search engine and content retrieval module.

Provides zero-dependency web search snippet retrieval, full URL content extraction
with HTML sanitization, and SVG/media asset discovery using standard library utilities.

SSRF hardening: this module fetches arbitrary URLs (search results can point anywhere,
including URLs an attacker controls via a poisoned page or prompt injection). Before
opening any connection, `_is_url_safe` rejects non-http(s) schemes and any hostname
that resolves to a private/loopback/link-local/reserved IP (127.0.0.1, internal LAN
ranges, cloud metadata endpoints like 169.254.169.254, etc.). Redirects are re-validated
per hop through a custom opener, and response bodies are capped in size.

Residual limitation: validation happens at request time using the IP(s) the hostname
resolves to *then*; a DNS-rebinding attacker who controls their own domain could in
theory change the DNS answer between our check and the actual connect. Full protection
against that requires pinning the validated IP for the actual socket connect (bypassing
urllib's built-in re-resolution), which is a larger change - flagging this here as a
known gap rather than silently leaving it undocumented.
"""

from __future__ import annotations

import html
import ipaddress
import json
import logging
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/115.0.0.0 Safari/537.36"
)
DEFAULT_TIMEOUT = 10.0

ALLOWED_SCHEMES = {"http", "https"}
MAX_CONTENT_BYTES = 2_000_000  # 2 MB cap per fetched page - avoids unbounded memory use
MAX_REDIRECTS = 3


def _is_ip_blocked(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        # Can't parse it - fail closed rather than let something unrecognized through.
        return True
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _is_url_safe(url: str) -> Tuple[bool, str]:
    """Blocks SSRF: only http/https schemes, and only hosts that resolve
    exclusively to public IPs (no localhost, no internal LAN, no cloud
    metadata endpoints)."""
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception as e:
        return False, f"Could not parse URL: {e}"

    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        return False, f"Blocked scheme '{parsed.scheme}' (only http/https allowed)"

    hostname = parsed.hostname
    if not hostname:
        return False, "URL has no hostname"

    try:
        addr_infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as e:
        return False, f"DNS resolution failed: {e}"
    except Exception as e:
        return False, f"DNS resolution error: {e}"

    resolved_ips = {info[4][0] for info in addr_infos}
    if not resolved_ips:
        return False, "No IP addresses resolved for host"

    for ip_str in resolved_ips:
        if _is_ip_blocked(ip_str):
            return False, f"Host resolves to a blocked/internal address ({ip_str})"

    return True, "ok"


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Re-validates every redirect hop instead of blindly following it.
    Without this, an initial URL can pass validation and then 302 to an
    internal address, bypassing the check entirely."""

    max_redirections = MAX_REDIRECTS

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        safe, reason = _is_url_safe(newurl)
        if not safe:
            raise urllib.error.URLError(f"Blocked unsafe redirect to {newurl!r}: {reason}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _build_safe_opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(_SafeRedirectHandler)


def _read_capped(response, max_bytes: int = MAX_CONTENT_BYTES) -> bytes:
    """Reads a response body up to max_bytes, ignoring whatever Content-Length claims."""
    chunks = []
    total = 0
    while total < max_bytes:
        chunk = response.read(65536)
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks)


class _HTMLTextExtractor(HTMLParser):
    """HTML Parser that strips script/style tags and extracts clean text and links."""

    def __init__(self) -> None:
        super().__init__()
        self.result: List[str] = []
        self.title: str = ""
        self._in_title = False
        self._skip_depth = 0
        self.links: List[Dict[str, str]] = []
        self.svgs: List[str] = []
        self.images: List[str] = []
        self._current_svg: Optional[List[str]] = None

    def handle_starttag(self, tag: str, attrs: List[tuple[str, Optional[str]]]) -> None:
        tag_lower = tag.lower()
        attr_dict = {k.lower(): (v or "") for k, v in attrs}

        if tag_lower in ("script", "style", "noscript", "svg"):
            if tag_lower == "svg":
                self._current_svg = [f"<{tag}"]
                for k, v in attrs:
                    self._current_svg.append(f' {k}="{html.escape(v or "")}"')
                self._current_svg.append(">")
            self._skip_depth += 1

        if tag_lower == "title":
            self._in_title = True

        if tag_lower == "a" and "href" in attr_dict:
            href = attr_dict["href"]
            self.links.append({"href": href, "text": ""})

        if tag_lower == "img" and "src" in attr_dict:
            self.images.append(attr_dict["src"])

    def handle_endtag(self, tag: str) -> None:
        tag_lower = tag.lower()
        if tag_lower == "title":
            self._in_title = False

        if tag_lower == "svg" and self._current_svg is not None:
            self._current_svg.append("</svg>")
            self.svgs.append("".join(self._current_svg))
            self._current_svg = None

        if tag_lower in ("script", "style", "noscript", "svg"):
            if self._skip_depth > 0:
                self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data

        if self._current_svg is not None:
            self._current_svg.append(data)

        if self._skip_depth == 0:
            cleaned = data.strip()
            if cleaned:
                self.result.append(cleaned)
                if self.links:
                    self.links[-1]["text"] = (self.links[-1]["text"] + " " + cleaned).strip()

    def get_text(self) -> str:
        return " ".join(self.result)


class WebSearchEngine:
    """Zero-dependency search engine and content extractor."""

    def __init__(
        self,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.user_agent = user_agent
        self.timeout = timeout
        self._opener = _build_safe_opener()

    def _build_request(self, url: str) -> urllib.request.Request:
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        return urllib.request.Request(url, headers=headers)

    def search(self, query: str, num_results: int = 5) -> List[Dict[str, str]]:
        """Search the web for snippets and URLs matching the query."""
        results: List[Dict[str, str]] = []
        encoded_query = urllib.parse.quote_plus(query)
        search_url = f"https://html.duckduckgo.com/html/?q={encoded_query}"

        safe, reason = _is_url_safe(search_url)
        if not safe:
            logger.error(f"Search URL blocked ({reason}): {search_url}")
            return []

        try:
            req = self._build_request(search_url)
            with self._opener.open(req, timeout=self.timeout) as response:
                content = _read_capped(response).decode("utf-8", errors="replace")

            matches = re.findall(
                r'<a[^>]+class="result__url"[^>]+href="([^"]+)"[^>]*>([\s\S]*?)</a>[\s\S]*?'
                r'<a[^>]+class="result__snippet"[^>]*>([\s\S]*?)</a>',
                content,
                flags=re.IGNORECASE,
            )

            for href, title, snippet in matches[:num_results]:
                clean_title = re.sub(r"<[^>]+>", "", title).strip()
                clean_snippet = re.sub(r"<[^>]+>", "", snippet).strip()
                parsed_href = href
                if "uddg=" in href:
                    parsed_qs = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
                    if "uddg" in parsed_qs:
                        parsed_href = parsed_qs["uddg"][0]

                results.append(
                    {
                        "title": html.unescape(clean_title),
                        "url": parsed_href,
                        "snippet": html.unescape(clean_snippet),
                    }
                )

        except Exception as e:
            logger.warning(f"Web search for '{query}' encountered error: {e}")
            return []

        if not results:
            logger.info(f"Web search for '{query}' returned zero parsed results.")

        return results[:num_results]

    def search_snippets(self, query: str, num_results: int = 5) -> List[Dict[str, str]]:
        """Alias for standard search returning snippet items."""
        return self.search(query, num_results=num_results)

    def fetch_content(self, url: str) -> Dict[str, Any]:
        """Fetch full URL content and sanitize HTML tags into plain text."""
        safe, reason = _is_url_safe(url)
        if not safe:
            logger.warning(f"Blocked fetch of unsafe URL {url!r}: {reason}")
            return {
                "url": url,
                "status": 403,
                "title": "Blocked",
                "content": "",
                "error": f"URL blocked by SSRF guard: {reason}",
                "raw_html": "",
            }

        try:
            req = self._build_request(url)
            with self._opener.open(req, timeout=self.timeout) as response:
                status_code = response.getcode()
                raw_bytes = _read_capped(response)
                raw_html = raw_bytes.decode("utf-8", errors="replace")

            extractor = _HTMLTextExtractor()
            extractor.feed(raw_html)

            return {
                "url": url,
                "status": status_code,
                "title": html.unescape(extractor.title.strip()),
                "content": extractor.get_text(),
                "raw_html": raw_html,
            }
        except Exception as e:
            logger.error(f"Failed to fetch content from {url}: {e}")
            return {
                "url": url,
                "status": 500,
                "title": "Error",
                "content": "",
                "error": str(e),
                "raw_html": "",
            }

    def extract_media(self, url: str) -> Dict[str, Any]:
        """Extract SVG assets, image links, and hyperlinks from a webpage URL."""
        content_res = self.fetch_content(url)
        raw_html = content_res.get("raw_html", "")

        extractor = _HTMLTextExtractor()
        if raw_html:
            extractor.feed(raw_html)

        inline_svgs = re.findall(r"(<svg[\s\S]*?</svg>)", raw_html, flags=re.IGNORECASE)
        all_svgs = list(dict.fromkeys(extractor.svgs + inline_svgs))

        return {
            "url": url,
            "title": content_res.get("title", ""),
            "svgs": all_svgs,
            "images": list(dict.fromkeys(extractor.images)),
            "links": extractor.links,
        }

    def search_media(
        self, query: str, media_type: str = "svg", num_results: int = 5
    ) -> List[Dict[str, Any]]:
        """Search query and extract media assets from resulting top web pages."""
        search_results = self.search(f"{query} {media_type}", num_results=num_results)
        media_results = []

        for item in search_results:
            page_url = item.get("url", "")
            if not page_url or not page_url.startswith("http"):
                continue
            media_data = self.extract_media(page_url)
            media_results.append(
                {
                    "query": query,
                    "url": page_url,
                    "title": item.get("title", ""),
                    "snippet": item.get("snippet", ""),
                    "svgs": media_data.get("svgs", []),
                    "images": media_data.get("images", []),
                }
            )

        return media_results

def search_web(query: str, num_results: int = 5) -> List[Dict[str, str]]:
    engine = WebSearchEngine()
    return engine.search(query, num_results=num_results)


def fetch_page(url: str) -> Dict[str, Any]:
    engine = WebSearchEngine()
    return engine.fetch_content(url)


def extract_svg_assets(html_content: str) -> List[str]:
    """Extract all inline SVG tags from raw HTML content."""
    inline_svgs = re.findall(r"(<svg[\s\S]*?</svg>)", html_content, flags=re.IGNORECASE)
    return list(dict.fromkeys(inline_svgs))