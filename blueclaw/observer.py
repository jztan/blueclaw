"""Observer hooks for tool tracing, truncation, and accumulation."""

from __future__ import annotations

import os
import select
import sys
import time
from datetime import datetime, timezone
from typing import Any

from rich.console import Console
from strands.hooks import (
    AfterToolCallEvent,
    BeforeToolCallEvent,
    HookProvider,
    HookRegistry,
)

from blueclaw.models import TraceStep

TRUNCATION_LIMIT = 12_000
HEAD_SIZE = 8_000
TAIL_SIZE = 4_000
INPUT_SUMMARY_MAX = 200

ESC_SEQ_TIMEOUT = 0.05  # 50ms — standard Esc vs escape-sequence threshold
ESC_ESC_WINDOW = 1.0  # seconds between two Esc presses


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


def _summarize_input(tool_input: dict) -> dict:
    """Create a truncated summary of tool input."""
    summary = {}
    for key, value in tool_input.items():
        s = str(value)
        if len(s) > INPUT_SUMMARY_MAX:
            s = s[:INPUT_SUMMARY_MAX] + "..."
        summary[key] = s
    return summary


def _summarize_output(result: Any, max_len: int = 200) -> str | None:
    """Extract a short summary from a tool result."""
    if result is None:
        return None
    content = result.get("content", []) if isinstance(result, dict) else []
    if not isinstance(content, list):
        return None
    for entry in content:
        text = entry.get("text", "") if isinstance(entry, dict) else ""
        if text:
            return text[:max_len] + ("..." if len(text) > max_len else "")
    return None


class ObserverHooks(HookProvider):
    """Hook provider for tool tracing, output truncation, and user interrupt."""

    def __init__(self, console: Console, quiet: bool = False) -> None:
        self.console = console
        self.quiet = quiet
        self._cancelled = False
        self._last_esc = 0.0
        self.tools_called: list[str] = []
        self.trace_steps: list[TraceStep] = []
        self._step_starts: dict[str, tuple[float, int, dict]] = {}
        self.mcp_clients: list = []  # Track MCP clients for cleanup

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(BeforeToolCallEvent, self.before_tool)
        registry.add_callback(AfterToolCallEvent, self.after_tool)

    # --- Escape detection ---

    def _check_escape(self) -> None:
        """Non-blocking check for double-Esc on stdin."""
        if not sys.stdin.isatty():
            return
        try:
            import termios
            import tty
        except ImportError:
            return

        fd = sys.stdin.fileno()
        try:
            old = termios.tcgetattr(fd)
        except termios.error:
            return

        try:
            tty.setcbreak(fd, when=termios.TCSANOW)
            while select.select([fd], [], [], 0)[0]:
                ch = os.read(fd, 1)
                if ch == b"\x1b":
                    # Peek: escape sequence (\x1b[…) or standalone Esc?
                    if select.select([fd], [], [], ESC_SEQ_TIMEOUT)[0]:
                        nxt = os.read(fd, 1)
                        if nxt == b"\x1b":
                            # Fast double-Esc (both in buffer)
                            self._cancelled = True
                            return
                        # Escape sequence — consume remainder
                        while select.select([fd], [], [], 0.01)[0]:
                            os.read(fd, 1)
                        continue
                    # Standalone Esc — check against previous
                    now = time.time()
                    if now - self._last_esc < ESC_ESC_WINDOW:
                        self._cancelled = True
                        return
                    self._last_esc = now
                # Non-Esc bytes during agent run are silently consumed
        except (OSError, ValueError):
            pass
        finally:
            try:
                import termios as _t

                _t.tcsetattr(fd, _t.TCSADRAIN, old)
            except Exception:
                pass

    # --- Hook callbacks ---

    def before_tool(self, event: BeforeToolCallEvent) -> None:
        tool_use = event.tool_use
        tool_id = tool_use["toolUseId"]
        tool_name = tool_use["name"]
        tool_input = tool_use.get("input", {})

        # Check for user interrupt (Esc Esc)
        self._check_escape()
        if self._cancelled:
            event.cancel_tool = "Cancelled by user."
            rs = event.invocation_state.setdefault("request_state", {})
            rs["stop_event_loop"] = True
            if not self.quiet:
                self.console.print(
                    "  [yellow]\u26a0 Stopped by user (Esc Esc)[/yellow]"
                )
            return

        step_index = len(self.trace_steps) + 1
        input_summary = _summarize_input(tool_input)
        self._step_starts[tool_id] = (time.time(), step_index, input_summary)

        if not self.quiet:
            input_str = str(tool_input)
            if len(input_str) > 60:
                input_str = input_str[:57] + "..."
            self.console.print(f"\u25cf {tool_name}({input_str})")

    def after_tool(self, event: AfterToolCallEvent) -> None:
        tool_use = event.tool_use
        tool_id = tool_use["toolUseId"]
        tool_name = tool_use["name"]

        start_ts = 0.0
        step_index = len(self.trace_steps) + 1
        input_summary: dict = {}

        if tool_id in self._step_starts:
            start_ts, step_index, input_summary = self._step_starts.pop(tool_id)

        end_ts = time.time()
        elapsed = end_ts - start_ts if start_ts else 0.0
        duration_ms = int(elapsed * 1000)

        status = "error" if event.exception else "success"
        error_msg = str(event.exception)[:200] if event.exception else None

        # Extract output summary before truncation
        output_summary = (
            _summarize_output(event.result) if not event.exception else None
        )

        if event.exception:
            if not self.quiet:
                err_msg = str(event.exception)
                if len(err_msg) > 80:
                    err_msg = err_msg[:77] + "..."
                self.console.print(f"  \u2717 {tool_name} {err_msg} ({elapsed:.1f}s)")
        else:
            if not self.quiet:
                self.console.print(f"  \u2713 {tool_name} {elapsed:.1f}s")
            # Truncate tool result content
            if event.result:
                truncate_tool_result(event.result)

        step = TraceStep(
            index=step_index,
            tool_name=tool_name,
            status=status,
            start_time=datetime.fromtimestamp(start_ts, tz=timezone.utc),
            end_time=datetime.fromtimestamp(end_ts, tz=timezone.utc),
            duration_ms=duration_ms,
            input_summary=input_summary,
            output_summary=output_summary,
            error=error_msg,
        )
        self.trace_steps.append(step)
        self.tools_called.append(tool_name)

    def reset(self) -> None:
        """Clear accumulated state between agent turns."""
        self.tools_called.clear()
        self.trace_steps.clear()
        self._step_starts.clear()
        self._cancelled = False
        self._last_esc = 0.0
