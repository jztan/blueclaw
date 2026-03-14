"""Tests for blueclaw.observer — hooks, truncation, accumulator."""

import time
from io import StringIO
from unittest.mock import Mock

import pytest
from rich.console import Console

from blueclaw.observer import (
    HEAD_SIZE,
    TAIL_SIZE,
    TRUNCATION_LIMIT,
    ObserverHooks,
    truncate_tool_result,
)


# --- Construction ---


class TestObserverConstruction:
    def test_observer_hooks_registers_two_callbacks(self):
        console = Console(file=StringIO())
        obs = ObserverHooks(console=console)
        registry = Mock()
        obs.register_hooks(registry)
        assert registry.add_callback.call_count == 2

    def test_observer_initial_state(self):
        console = Console(file=StringIO())
        obs = ObserverHooks(console=console)
        assert obs.tools_called == []


# --- Before tool call ---


class TestBeforeTool:
    def test_before_tool_prints_tool_name(self, mock_before_event):
        output = StringIO()
        console = Console(file=output)
        obs = ObserverHooks(console=console)
        obs.before_tool(mock_before_event)
        assert "tool_name" in output.getvalue()

    def test_before_tool_truncates_long_input(self, mock_before_event):
        output = StringIO()
        console = Console(file=output)
        obs = ObserverHooks(console=console)
        mock_before_event.tool_use["input"] = {"query": "x" * 100}
        obs.before_tool(mock_before_event)
        text = output.getvalue()
        # Input display should be truncated
        assert "..." in text or len(text) < 200

    def test_before_tool_stores_start_time(self, mock_before_event):
        console = Console(file=StringIO())
        obs = ObserverHooks(console=console)
        obs.before_tool(mock_before_event)
        assert "id123" in obs._step_starts
        start_ts, step_index, input_summary = obs._step_starts["id123"]
        assert isinstance(start_ts, float)


# --- After tool call (success) ---


class TestAfterToolSuccess:
    def test_after_tool_prints_success(self, mock_before_event, mock_after_event):
        output = StringIO()
        console = Console(file=output)
        obs = ObserverHooks(console=console)
        # Simulate before to set start time
        obs.before_tool(mock_before_event)
        obs.after_tool(mock_after_event)
        text = output.getvalue()
        assert "\u2713" in text  # checkmark

    def test_after_tool_elapsed_time_format(self, mock_before_event, mock_after_event):
        output = StringIO()
        console = Console(file=output)
        obs = ObserverHooks(console=console)
        obs.before_tool(mock_before_event)
        obs.after_tool(mock_after_event)
        text = output.getvalue()
        # Should contain something like "0.0s"
        assert "s" in text

    def test_after_tool_accumulates_tool_name(
        self, mock_before_event, mock_after_event
    ):
        console = Console(file=StringIO())
        obs = ObserverHooks(console=console)
        obs.before_tool(mock_before_event)
        obs.after_tool(mock_after_event)
        assert "tool_name" in obs.tools_called


# --- After tool call (failure) ---


class TestAfterToolFailure:
    def test_after_tool_prints_failure(self, mock_before_event, mock_after_event):
        output = StringIO()
        console = Console(file=output)
        obs = ObserverHooks(console=console)
        obs.before_tool(mock_before_event)
        mock_after_event.exception = RuntimeError("something broke")
        obs.after_tool(mock_after_event)
        text = output.getvalue()
        assert "\u2717" in text  # X mark

    def test_after_tool_prints_error_truncated(
        self, mock_before_event, mock_after_event
    ):
        output = StringIO()
        console = Console(file=output)
        obs = ObserverHooks(console=console)
        obs.before_tool(mock_before_event)
        mock_after_event.exception = RuntimeError("E" * 200)
        obs.after_tool(mock_after_event)
        text = output.getvalue()
        # Error message should be truncated
        assert len(text) < 300


# --- Truncation ---


class TestTruncation:
    def test_truncate_short_output(self):
        result = {
            "toolUseId": "123",
            "status": "success",
            "content": [{"text": "short output"}],
        }
        truncated = truncate_tool_result(result)
        assert truncated["content"][0]["text"] == "short output"

    def test_truncate_long_output(self):
        long_text = "x" * 20000
        result = {
            "toolUseId": "123",
            "status": "success",
            "content": [{"text": long_text}],
        }
        truncated = truncate_tool_result(result)
        text = truncated["content"][0]["text"]
        assert len(text) < 20000
        assert text.startswith("x" * 100)
        assert text.endswith("x" * 100)

    def test_truncate_marker_includes_char_count(self):
        long_text = "x" * 20000
        result = {
            "toolUseId": "123",
            "status": "success",
            "content": [{"text": long_text}],
        }
        truncated = truncate_tool_result(result)
        text = truncated["content"][0]["text"]
        assert "truncated" in text
        # Should mention the number of chars removed
        removed = 20000 - HEAD_SIZE - TAIL_SIZE
        assert str(removed) in text

    def test_truncate_preserves_tool_use_id(self):
        result = {
            "toolUseId": "abc",
            "status": "success",
            "content": [{"text": "x" * 20000}],
        }
        truncated = truncate_tool_result(result)
        assert truncated["toolUseId"] == "abc"

    def test_truncate_targets_text_content(self):
        result = {
            "toolUseId": "123",
            "status": "success",
            "content": [{"text": "x" * 20000}],
        }
        truncated = truncate_tool_result(result)
        assert "text" in truncated["content"][0]

    def test_truncate_exactly_at_limit(self):
        text = "x" * TRUNCATION_LIMIT
        result = {
            "toolUseId": "123",
            "status": "success",
            "content": [{"text": text}],
        }
        truncated = truncate_tool_result(result)
        assert truncated["content"][0]["text"] == text

    def test_truncate_just_over_limit(self):
        text = "x" * (TRUNCATION_LIMIT + 1)
        result = {
            "toolUseId": "123",
            "status": "success",
            "content": [{"text": text}],
        }
        truncated = truncate_tool_result(result)
        assert "truncated" in truncated["content"][0]["text"]
        # Head + tail preserved, original content replaced
        assert truncated["content"][0]["text"] != text

    def test_truncate_multipart_content(self):
        result = {
            "toolUseId": "123",
            "status": "success",
            "content": [
                {"text": "x" * 20000},
                {"image": "base64data"},
                {"text": "short"},
            ],
        }
        truncated = truncate_tool_result(result)
        # Long text truncated
        assert "truncated" in truncated["content"][0]["text"]
        # Non-text entry preserved
        assert truncated["content"][1] == {"image": "base64data"}
        # Short text unchanged
        assert truncated["content"][2]["text"] == "short"


# --- Accumulator ---


class TestAccumulator:
    def test_tools_called_accumulates(self):
        console = Console(file=StringIO())
        obs = ObserverHooks(console=console)
        for i in range(3):
            before = Mock()
            before.tool_use = {
                "name": f"tool_{i}",
                "input": {},
                "toolUseId": f"id_{i}",
            }
            before.cancel_tool = False
            after = Mock()
            after.tool_use = before.tool_use
            after.result = {
                "toolUseId": f"id_{i}",
                "status": "success",
                "content": [{"text": "ok"}],
            }
            after.exception = None
            obs.before_tool(before)
            obs.after_tool(after)
        assert len(obs.tools_called) == 3

    def test_reset_clears_accumulator(self):
        console = Console(file=StringIO())
        obs = ObserverHooks(console=console)
        obs.tools_called.append("test")
        obs._step_starts["abc"] = (time.time(), 1, {})
        obs.trace_steps.append(None)
        obs.reset()
        assert obs.tools_called == []
        assert obs._step_starts == {}
        assert obs.trace_steps == []


# --- Quiet mode ---


class TestQuietMode:
    def test_quiet_mode_suppresses_console(self, mock_before_event, mock_after_event):
        output = StringIO()
        console = Console(file=output)
        obs = ObserverHooks(console=console, quiet=True)
        obs.before_tool(mock_before_event)
        obs.after_tool(mock_after_event)
        assert output.getvalue().strip() == ""

    def test_quiet_mode_still_accumulates(self, mock_before_event, mock_after_event):
        console = Console(file=StringIO())
        obs = ObserverHooks(console=console, quiet=True)
        obs.before_tool(mock_before_event)
        obs.after_tool(mock_after_event)
        assert "tool_name" in obs.tools_called
