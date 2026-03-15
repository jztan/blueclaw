"""Web tools — search and HTTP request with domain allowlist."""

from __future__ import annotations

from urllib.parse import urlparse
from urllib.request import urlopen

from strands import tool


def make_web_search():
    """Factory that returns a configured web_search tool."""

    @tool
    def web_search(query: str) -> str:
        """Search the web for a query and return results."""
        # Placeholder — real implementation (DuckDuckGo) coming later.
        return (
            "Web search is not available yet. "
            "Answer from your own knowledge or tell the user "
            "you cannot look this up right now."
        )

    return web_search


def make_http_request(allowlist: list[str]):
    """Factory that returns an http_request tool with domain allowlist enforcement."""

    @tool
    def http_request(url: str) -> str:
        """Fetch a URL. Domain must be in the allowlist."""
        domain = urlparse(url).hostname
        if domain not in allowlist:
            return f"Error: Domain {domain} not in allowlist"
        with urlopen(url) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(charset)

    return http_request
