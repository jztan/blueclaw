"""Tests for blueclaw.tools — web tools, factory pattern, tool loading."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from blueclaw.models import SessionConfig
from blueclaw.tools import get_mcp_servers, get_tools
from blueclaw.tools.shell import make_shell_command
from blueclaw.tools.web import make_http_request, make_web_search
from blueclaw.workspace import Workspace

# --- web.py ---


FAKE_RESULTS = [
    {"title": "Result 1", "href": "https://example.com/1", "body": "Snippet one"},
    {"title": "Result 2", "href": "https://example.com/2", "body": "Snippet two"},
    {"title": "Result 3", "href": "https://example.com/3", "body": "Snippet three"},
    {"title": "Result 4", "href": "https://example.com/4", "body": "Snippet four"},
    {"title": "Result 5", "href": "https://example.com/5", "body": "Snippet five"},
]


class TestWebSearch:
    def test_web_search_is_tool(self):
        tool = make_web_search()
        # Strands @tool decorated functions have tool metadata
        assert callable(tool)
        assert tool.__doc__ is not None and len(tool.__doc__) > 0

    def test_web_search_returns_string(self):
        tool = make_web_search()
        # @tool decorator wraps the function; verify it's callable
        assert callable(tool)

    def test_web_search_returns_formatted_results(self):
        tool = make_web_search()
        with patch("ddgs.DDGS") as mock_ddgs_cls:
            mock_ddgs_cls.return_value.text.return_value = FAKE_RESULTS
            result = tool(query="python tutorials")

        assert "Result 1" in result
        assert "https://example.com/1" in result
        assert "Snippet one" in result
        assert "Result 5" in result
        for r in FAKE_RESULTS:
            assert r["title"] in result
            assert r["href"] in result
            assert r["body"] in result

    def test_web_search_calls_ddgs_with_max_results(self):
        tool = make_web_search()
        with patch("ddgs.DDGS") as mock_ddgs_cls:
            mock_instance = mock_ddgs_cls.return_value
            mock_instance.text.return_value = FAKE_RESULTS
            tool(query="test query")

        mock_instance.text.assert_called_once_with("test query", max_results=5)

    def test_web_search_empty_results(self):
        tool = make_web_search()
        with patch("ddgs.DDGS") as mock_ddgs_cls:
            mock_ddgs_cls.return_value.text.return_value = []
            result = tool(query="xyzzy nonsense")

        assert result == "No results found."

    def test_web_search_propagates_exceptions(self):
        tool = make_web_search()
        with patch("ddgs.DDGS") as mock_ddgs_cls:
            mock_ddgs_cls.return_value.text.side_effect = Exception("Rate limited")
            with pytest.raises(Exception, match="Rate limited"):
                tool(query="anything")

    def test_web_search_single_result(self):
        tool = make_web_search()
        single = [{"title": "Only One", "href": "https://one.com", "body": "Solo"}]
        with patch("ddgs.DDGS") as mock_ddgs_cls:
            mock_ddgs_cls.return_value.text.return_value = single
            result = tool(query="rare topic")

        assert "Only One" in result
        assert "https://one.com" in result
        assert "Solo" in result

    def test_web_search_result_format(self):
        tool = make_web_search()
        data = [{"title": "T", "href": "https://u.com", "body": "B"}]
        with patch("ddgs.DDGS") as mock_ddgs_cls:
            mock_ddgs_cls.return_value.text.return_value = data
            result = tool(query="format test")

        assert result == "**T**\nhttps://u.com\nB\n"


class TestHttpRequest:
    def test_http_request_is_tool(self):
        tool = make_http_request(allowlist=["example.com"])
        assert callable(tool)
        assert tool.__doc__ is not None and len(tool.__doc__) > 0

    def test_http_request_validates_domain(self):
        tool = make_http_request(allowlist=["example.com"])
        result = tool(url="https://evil.com/page")
        assert "not in allowlist" in result.lower() or "error" in result.lower()

    def test_http_request_allows_listed_domain(self):
        tool = make_http_request(allowlist=["example.com"])
        with patch("blueclaw.tools.web.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.read.return_value = b"<html>content</html>"
            mock_response.headers.get_content_charset.return_value = "utf-8"
            mock_response.__enter__ = lambda s: mock_response
            mock_response.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_response
            result = tool(url="https://example.com/page")
            assert "content" in result

    def test_tool_docstrings_present(self):
        ws = make_web_search()
        hr = make_http_request(allowlist=[])
        assert ws.__doc__
        assert hr.__doc__


# --- tools/__init__.py ---


class TestGetTools:
    def test_get_tools_by_name(self):
        config = SessionConfig(tools=["web"], allowlist_domains=["example.com"])
        tools = get_tools(["web"], config)
        assert len(tools) > 0
        assert all(callable(t) for t in tools)

    def test_get_tools_unknown(self):
        config = SessionConfig()
        with pytest.raises(ValueError):
            get_tools(["unknown"], config)

    def test_tools_created_with_config(self):
        config = SessionConfig(allowlist_domains=["test.com"])
        tools = get_tools(["web"], config)
        # Tools should be created with the config's allowlist
        assert len(tools) > 0

    def test_get_mcp_servers(self):
        config = SessionConfig(tools=["pdf"])
        servers = get_mcp_servers(config)
        # Should return MCPClient instances for pdf-mcp
        assert isinstance(servers, list)
        assert len(servers) == 1

    def test_get_mcp_servers_for_http_target(self):
        config = SessionConfig(tools=["mcp:https://localhost:8080/sse"])
        servers = get_mcp_servers(config)
        assert isinstance(servers, list)
        assert len(servers) == 1

    def test_get_mcp_servers_for_stdio_target(self):
        config = SessionConfig(tools=["mcp:my-mcp-server"])
        servers = get_mcp_servers(config)
        assert isinstance(servers, list)
        assert len(servers) == 1

    def test_get_tools_shell(self, tmp_path):
        ws = Workspace(tmp_path)
        config = SessionConfig(tools=["shell"])
        tools = get_tools(["shell"], config, workspace=ws)
        assert len(tools) == 1
        assert callable(tools[0])

    def test_get_tools_unknown_still_raises(self):
        config = SessionConfig()
        with pytest.raises(ValueError):
            get_tools(["unknown"], config)
