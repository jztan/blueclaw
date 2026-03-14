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
        # In v1, this is a placeholder — real implementation uses
        # strands-agents-tools or an MCP server for search.
        return f"Search results for: {query}"

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
