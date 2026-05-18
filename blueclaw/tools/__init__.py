"""Tool loading and MCP server configuration."""

from __future__ import annotations

import os
from typing import Callable
from urllib.parse import urlparse

from blueclaw.models import SessionConfig
from blueclaw.tools.shell import make_shell_command
from blueclaw.tools.web import make_http_request, make_web_search

TOOL_REGISTRY: dict[str, Callable] = {
    "web": lambda config, workspace: [
        make_web_search(),
        make_http_request(
            config.allowlist_domains, extract_main=config.http_extract_main
        ),
    ],
    "shell": lambda config, workspace: [make_shell_command(workspace)],
}


def get_tools(
    names: list[str], config: SessionConfig, workspace=None
) -> list[Callable]:
    """Create configured tool instances based on names and config."""
    tools = []
    for name in names:
        if name in TOOL_REGISTRY:
            tools.extend(TOOL_REGISTRY[name](config, workspace))
        elif name.startswith("mcp:"):
            continue  # MCP tools handled separately
        elif name == "pdf":
            continue  # Handled via MCP server
        else:
            raise ValueError(f"Unknown tool: {name}")
    return tools


def get_mcp_servers(config: SessionConfig) -> list:
    """Return MCPClient instances for bundled MCP servers."""
    from strands.tools.mcp import MCPClient
    from mcp.client.sse import sse_client
    from mcp.client.stdio import StdioServerParameters, stdio_client

    def stdio_params(command: str) -> StdioServerParameters:
        env = dict(os.environ)
        env["FASTMCP_SHOW_SERVER_BANNER"] = "false"
        env["FASTMCP_CHECK_FOR_UPDATES"] = "off"
        env["FASTMCP_LOG_LEVEL"] = "WARNING"
        env["FASTMCP_ENABLE_RICH_LOGGING"] = "false"
        return StdioServerParameters(command=command, args=[], env=env)

    servers = []
    if "pdf" in config.tools:
        servers.append(MCPClient(lambda: stdio_client(stdio_params("pdf-mcp"))))
    # Add custom MCP servers from config
    for tool_name in config.tools:
        if tool_name.startswith("mcp:"):
            target = tool_name[4:]
            parsed = urlparse(target)
            if parsed.scheme in {"http", "https"}:
                servers.append(MCPClient(lambda u=target: sse_client(u)))
            else:
                servers.append(
                    MCPClient(lambda cmd=target: stdio_client(stdio_params(cmd)))
                )
    return servers
