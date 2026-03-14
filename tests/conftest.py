"""Shared test fixtures for blueclaw tests."""

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest


@pytest.fixture
def tmp_workspace(tmp_path):
    """Create a temp directory with workspace structure."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    blueclaw_dir = ws / ".blueclaw"
    blueclaw_dir.mkdir()
    (ws / "CONTEXT.md").write_text("# Context\nTest workspace context.\n")
    (blueclaw_dir / "history.jsonl").write_text("")
    return ws


@pytest.fixture
def sample_config():
    """Return a SessionConfig with test defaults."""
    from blueclaw.models import SessionConfig

    return SessionConfig(
        provider="anthropic",
        model_id="claude-sonnet-4-6",
        max_tokens=4096,
        workspace_path=Path("/tmp/test-workspace"),
        allowlist_domains=["example.com"],
        tools=["web"],
    )


@pytest.fixture
def sample_run_record():
    """Return a RunRecord with realistic test data."""
    from blueclaw.models import RunRecord

    return RunRecord(
        ts=datetime(2026, 3, 14, 12, 0, 0, tzinfo=timezone.utc),
        goal="Search for Python docs",
        tools=["web_search", "http_request"],
        files=["notes.md"],
        tokens=150,
        cost=0.0023,
    )


@pytest.fixture
def mock_agent():
    """Patch strands.Agent — returns object where __call__ returns AgentResult."""
    agent = MagicMock()
    result = MagicMock()
    result.message = "response text"
    result.metrics.accumulated_usage = {
        "inputTokens": 100,
        "outputTokens": 50,
        "totalTokens": 150,
    }
    result.stop_reason = "end_turn"
    agent.return_value = result
    return agent


@pytest.fixture
def mock_before_event():
    """Create BeforeToolCallEvent mock with correct structure."""
    event = Mock()
    event.tool_use = {
        "name": "tool_name",
        "input": {"key": "value"},
        "toolUseId": "id123",
    }
    event.selected_tool = None
    event.invocation_state = {}
    event.cancel_tool = False
    return event


@pytest.fixture
def mock_after_event():
    """Create AfterToolCallEvent mock with correct ToolResult structure."""
    event = Mock()
    event.tool_use = {
        "name": "tool_name",
        "input": {"key": "value"},
        "toolUseId": "id123",
    }
    event.selected_tool = None
    event.result = {
        "toolUseId": "id123",
        "status": "success",
        "content": [{"text": "output"}],
    }
    event.exception = None
    event.invocation_state = {}
    return event
