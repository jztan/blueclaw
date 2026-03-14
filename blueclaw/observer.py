"""Observer hooks for tool tracing, truncation, and accumulation."""

from __future__ import annotations

import time
from typing import Any

from rich.console import Console
from strands.hooks import (
    AfterToolCallEvent,
    BeforeToolCallEvent,
    HookProvider,
    HookRegistry,
)

TRUNCATION_LIMIT = 12_000
HEAD_SIZE = 8_000
TAIL_SIZE = 4_000


def truncate_tool_result(result: dict) -> dict:
    """Truncate text content entries that exceed the limit."""
    content = result.get("content", [])
    for entry in content:
        if "text" in entry and len(entry["text"]) > TRUNCATION_LIMIT:
            text = entry["text"]
            removed = len(text) - HEAD_SIZE - TAIL_SIZE
            entry["text"] = (
                text[:HEAD_SIZE]
                + f"\n... [truncated {removed} chars] ...\n"
                + text[-TAIL_SIZE:]
            )
    return result


class ObserverHooks(HookProvider):
    """Hook provider for tool tracing and output truncation."""

    def __init__(self, console: Console, quiet: bool = False) -> None:
        self.console = console
        self.quiet = quiet
        self.tools_called: list[str] = []
        self._start_times: dict[str, float] = {}
        self.mcp_clients: list = []  # Track MCP clients for cleanup

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(BeforeToolCallEvent, self.before_tool)
        registry.add_callback(AfterToolCallEvent, self.after_tool)

    def before_tool(self, event: BeforeToolCallEvent) -> None:
        tool_use = event.tool_use
        tool_id = tool_use["toolUseId"]
        tool_name = tool_use["name"]
        self._start_times[tool_id] = time.time()

        if not self.quiet:
            input_str = str(tool_use.get("input", {}))
            if len(input_str) > 60:
                input_str = input_str[:57] + "..."
            self.console.print(f"\u25cf {tool_name}({input_str})")

    def after_tool(self, event: AfterToolCallEvent) -> None:
        tool_use = event.tool_use
        tool_id = tool_use["toolUseId"]
        tool_name = tool_use["name"]

        elapsed = 0.0
        if tool_id in self._start_times:
            elapsed = time.time() - self._start_times.pop(tool_id)

        if event.exception:
            if not self.quiet:
                err_msg = str(event.exception)
                if len(err_msg) > 80:
                    err_msg = err_msg[:77] + "..."
                self.console.print(f"  \u2717 {err_msg} ({elapsed:.1f}s)")
        else:
            if not self.quiet:
                self.console.print(f"  \u2713 {elapsed:.1f}s")
            # Truncate tool result content
            if event.result:
                truncate_tool_result(event.result)

        self.tools_called.append(tool_name)

    def reset(self) -> None:
        """Clear accumulated state between agent turns."""
        self.tools_called.clear()
        self._start_times.clear()
