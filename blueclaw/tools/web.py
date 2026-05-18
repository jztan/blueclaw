"""Web tools — search and HTTP request with domain allowlist."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from strands import tool

_IMPERSONATE_TARGET = "chrome124"

_WS_RE = re.compile(r"[ \t]+")
_BLANKLINE_RE = re.compile(r"\n{3,}")


def _extract_main_text(html: str) -> str:
    """Strip boilerplate and return article title + main text.

    Falls back to the raw html if extraction fails or the page doesn't
    look like an article. Small models drown in tag soup — this trims
    a typical news/blog page from ~80k tokens of DOM to ~1–3k tokens
    of body text.
    """
    try:
        import trafilatura
    except Exception:
        return html

    try:
        text = trafilatura.extract(
            html,
            include_comments=False,
            include_tables=True,
            favor_recall=True,
        )
        meta = trafilatura.extract_metadata(html)
        title = (meta.title if meta and meta.title else "").strip()
    except Exception:
        return html

    if not text:
        return html
    text = _BLANKLINE_RE.sub("\n\n", text).strip()
    return f"{title}\n\n{text}" if title else text


def make_web_search():
    """Factory that returns a configured web_search tool."""

    @tool
    def web_search(query: str) -> str:
        """Search the web for a query and return results."""
        from ddgs import DDGS

        results = DDGS().text(query, max_results=5)
        if not results:
            return "No results found."
        lines = []
        for r in results:
            lines.append(f"**{r['title']}**\n{r['href']}\n{r['body']}\n")
        return "\n".join(lines)

    return web_search


def make_http_request(allowlist: list[str], extract_main: bool = True):
    """Factory that returns an http_request tool with domain allowlist enforcement.

    When ``extract_main`` is True (default), HTML responses are run through a
    readability extractor that returns just the article title and body. Set
    to False to receive the raw response (useful for APIs or when you need
    the full DOM).
    """

    @tool
    def http_request(url: str) -> str:
        """Fetch a URL. Domain must be in the allowlist."""
        domain = urlparse(url).hostname
        if domain not in allowlist:
            return f"Error: Domain {domain} not in allowlist"
        from curl_cffi import requests as curl_requests

        response = curl_requests.get(
            url,
            impersonate=_IMPERSONATE_TARGET,
            timeout=30,
            allow_redirects=True,
        )
        if response.status_code >= 400:
            return f"Error: HTTP {response.status_code} fetching {url}"
        content_type = (response.headers.get("content-type") or "").lower()
        body = response.text
        if extract_main and "html" in content_type:
            return _extract_main_text(body)
        return body

    return http_request
