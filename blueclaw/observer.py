"""Observer hooks for tool tracing, truncation, and accumulation."""

from __future__ import annotations

import os
import select
import sys
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from rich.console import Console
from strands.hooks import (
    AfterModelCallEvent,
    AfterToolCallEvent,
    BeforeModelCallEvent,
    BeforeToolCallEvent,
    HookProvider,
    HookRegistry,
    MessageAddedEvent,
)

from blueclaw.models import TraceStep

if TYPE_CHECKING:
    from blueclaw.events import EventBus

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


def _count_result_chars(result: Any) -> int:
    """Total text length across all content entries in a tool result.

    Shared by ObserverHooks.after_tool to emit `tool.after.output_chars` and
    by any future trace-finalization code that needs the same number.
    """
    if not isinstance(result, dict):
        return 0
    total = 0
    for entry in result.get("content", []) or []:
        if isinstance(entry, dict) and "text" in entry:
            total += len(entry["text"])
    return total


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

    def __init__(
        self,
        console: Console,
        quiet: bool = False,
        bus: "EventBus | None" = None,
    ) -> None:
        self.console = console
        self.quiet = quiet
        self.bus = bus  # settable per turn by adapters
        self._cancelled = False
        self._last_esc = 0.0
        self.tools_called: list[str] = []
        self.trace_steps: list[TraceStep] = []
        self._step_starts: dict[str, tuple[float, int, dict]] = {}
        self.mcp_clients: list = []  # Track MCP clients for cleanup

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(BeforeToolCallEvent, self.before_tool)
        registry.add_callback(AfterToolCallEvent, self.after_tool)
        registry.add_callback(BeforeModelCallEvent, self.before_model)
        registry.add_callback(AfterModelCallEvent, self.after_model)
        registry.add_callback(MessageAddedEvent, self.on_message_added)

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

        if self.bus is not None:
            self.bus.emit(
                {
                    "type": "tool.before",
                    "tool_name": tool_name,
                    "tool_use_id": tool_id,
                    "input": input_summary,
                }
            )

        if not self.quiet:
            input_str = str(tool_input)
            if len(input_str) > 60:
                input_str = input_str[:57] + "..."
            self.console.print(f"\u25cf {tool_name}({input_str})")

    def after_tool(self, event: AfterToolCallEvent) -> None:
        tool_use = event.tool_use
        tool_id = tool_use["toolUseId"]
        tool_name = tool_use["name"]
        output_chars = _count_result_chars(event.result)

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

        if self.bus is not None:
            self.bus.emit(
                {
                    "type": "tool.after",
                    "tool_name": tool_name,
                    "tool_use_id": tool_id,
                    "status": status,
                    "duration_ms": duration_ms,
                    "output_chars": output_chars,
                    "error": error_msg,
                }
            )

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
            sandbox=build_sandbox_metadata(),
        )
        self.trace_steps.append(step)
        self.tools_called.append(tool_name)

    def before_model(self, event: BeforeModelCallEvent) -> None:
        """Emit model.before event with agent context snapshot."""
        if self.bus is None:
            return
        try:
            agent = event.agent
            model_id = ""
            try:
                model_id = str(getattr(agent.model, "config", {}).get("model_id", ""))
            except (AttributeError, TypeError):
                pass

            prompt_messages = 0
            try:
                prompt_messages = len(agent.messages)
            except (AttributeError, TypeError):
                pass

            system_prompt_chars = 0
            try:
                sp = agent.system_prompt
                system_prompt_chars = len(sp) if sp else 0
            except (AttributeError, TypeError):
                pass

            tools_provided: list[str] = []
            try:
                tools_provided = list(agent.tool_names)
            except (AttributeError, TypeError):
                pass

            self.bus.emit(
                {
                    "type": "model.before",
                    "model_id": model_id,
                    "prompt_messages": prompt_messages,
                    "system_prompt_chars": system_prompt_chars,
                    "tools_provided": tools_provided,
                }
            )
        except Exception:
            pass

    def after_model(self, event: AfterModelCallEvent) -> None:
        """Emit model.after event with usage and timing metrics."""
        if self.bus is None:
            return
        try:
            duration_ms = 0
            input_tokens = 0
            output_tokens = 0
            cache_read = 0
            cache_creation = 0
            stop_reason: str | None = None

            sr = event.stop_response
            if sr is not None:
                try:
                    stop_reason = sr.stop_reason
                except (AttributeError, TypeError):
                    pass
                try:
                    metadata = sr.message.get("metadata", {}) or {}
                    usage = metadata.get("usage", {}) or {}
                    metrics = metadata.get("metrics", {}) or {}
                    input_tokens = int(usage.get("inputTokens", 0) or 0)
                    output_tokens = int(usage.get("outputTokens", 0) or 0)
                    cache_read = int(usage.get("cacheReadInputTokens", 0) or 0)
                    cache_creation = int(usage.get("cacheWriteInputTokens", 0) or 0)
                    duration_ms = int(metrics.get("latencyMs", 0) or 0)
                except (AttributeError, TypeError, KeyError):
                    pass

            self.bus.emit(
                {
                    "type": "model.after",
                    "duration_ms": duration_ms,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cache_read": cache_read,
                    "cache_creation": cache_creation,
                    "stop_reason": stop_reason,
                }
            )
        except Exception:
            pass

    def on_message_added(self, event: MessageAddedEvent) -> None:
        """Emit message.added event with role and content statistics."""
        if self.bus is None:
            return
        try:
            message = event.message
            role = ""
            text_chars = 0
            tool_uses = 0

            try:
                role = str(message.get("role", "") or "")
            except (AttributeError, TypeError):
                pass

            try:
                content = message.get("content", []) or []
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if "text" in block:
                        text_chars += len(block["text"] or "")
                    if "toolUse" in block:
                        tool_uses += 1
            except (AttributeError, TypeError):
                pass

            self.bus.emit(
                {
                    "type": "message.added",
                    "role": role,
                    "text_chars": text_chars,
                    "tool_uses": tool_uses,
                }
            )
        except Exception:
            pass

    def reset(self) -> None:
        """Clear accumulated state between agent turns."""
        self.tools_called.clear()
        self.trace_steps.clear()
        self._step_starts.clear()
        self._cancelled = False
        self._last_esc = 0.0


_SANDBOX_METADATA: dict[str, str | None] | None = None


def build_sandbox_metadata() -> dict[str, str | None]:
    """Construct TraceStep.sandbox from launcher-supplied env vars. Cached."""
    global _SANDBOX_METADATA
    if _SANDBOX_METADATA is None:
        _SANDBOX_METADATA = {
            "mode": os.environ.get("BLUECLAW_SANDBOX_MODE", "inprocess"),
            "image": os.environ.get("BLUECLAW_SANDBOX_IMAGE"),
            "image_digest": os.environ.get("BLUECLAW_SANDBOX_DIGEST"),
            "fallback_reason": os.environ.get("BLUECLAW_SANDBOX_FALLBACK_REASON"),
        }
    return _SANDBOX_METADATA


def _reset_sandbox_metadata_cache() -> None:
    """Test hook: forces the next build_sandbox_metadata() to re-read env."""
    global _SANDBOX_METADATA
    _SANDBOX_METADATA = None
