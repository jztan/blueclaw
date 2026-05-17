"""Unit tests for blueclaw/runner.py."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from blueclaw.models import SessionConfig
from blueclaw.runner import RunOutcome, _write_capture_artifacts, runner_session
from blueclaw.workspace import Workspace


def test_runoutcome_defaults_match_spec():
    """RunOutcome fields and types match the design spec contract."""
    o = RunOutcome(
        result=None,
        agent=None,
        response_text="",
        trace=None,
        record=None,
        capture_errors=[],
        error=None,
    )
    assert o.result is None
    assert o.response_text == ""
    assert o.trace is None
    assert o.record is None
    assert o.capture_errors == []
    assert o.error is None


def test_write_capture_artifacts_happy_path(tmp_path: Path):
    capture_path = tmp_path / "case-001" / "run-000"
    errs = _write_capture_artifacts(
        capture_path,
        response_text="hello",
        messages=[{"role": "user", "content": "hi"}],
    )
    assert errs == []
    assert (capture_path / "response.txt").read_text() == "hello"
    data = json.loads((capture_path / "messages.json").read_text())
    assert data == [{"role": "user", "content": "hi"}]


def test_write_capture_artifacts_mkdir_failure(tmp_path: Path):
    # Make tmp_path read-only so mkdir of a child fails on POSIX.
    readonly = tmp_path / "ro"
    readonly.mkdir()
    os.chmod(readonly, 0o500)
    try:
        errs = _write_capture_artifacts(
            readonly / "case-001" / "run-000",
            response_text="x",
            messages=[],
        )
        assert len(errs) == 1
        assert errs[0]["stage"] == "mkdir"
        assert "error" in errs[0]
    finally:
        os.chmod(readonly, 0o700)


def test_write_capture_artifacts_messages_serialization_fallback(tmp_path: Path):
    """Non-JSON-serializable objects fall back to str() via default=str."""

    class Weird:
        def __str__(self) -> str:
            return "WEIRD"

    errs = _write_capture_artifacts(
        tmp_path / "leaf",
        response_text="",
        messages=[{"role": "user", "content": Weird()}],
    )
    assert errs == []
    text = (tmp_path / "leaf" / "messages.json").read_text()
    assert "WEIRD" in text


# ---------------------------------------------------------------------------
# runner_session tests (Task 3)
# ---------------------------------------------------------------------------


def _fake_agent_factory():
    """Return a stand-in for strands.Agent — callable, has messages/state."""
    agent = MagicMock(name="fake_agent")
    agent.messages = []
    agent.state = MagicMock()
    return agent


@pytest.fixture
def fake_session(tmp_path: Path):
    """Minimal config + workspace fixture for runner tests."""
    workspace = Workspace(tmp_path / "ws")
    config = SessionConfig(provider="anthropic", model_id="claude-test")
    return config, workspace


def test_runner_session_yields_ctx_with_observer_and_agent(fake_session):
    config, workspace = fake_session
    fake_agent = _fake_agent_factory()
    with patch("blueclaw.runner.create_agent", return_value=fake_agent) as mk:
        with runner_session(config, workspace, model=MagicMock()) as ctx:
            assert ctx.agent is fake_agent
            assert ctx.observer is not None
        mk.assert_called_once()


def test_runner_session_cleanup_runs_on_normal_exit(fake_session):
    config, workspace = fake_session
    with (
        patch("blueclaw.runner.create_agent", return_value=_fake_agent_factory()),
        patch("blueclaw.runner.cleanup_mcp_clients") as mk_cleanup,
    ):
        with runner_session(config, workspace, model=MagicMock()) as ctx:
            pass
        mk_cleanup.assert_called_once_with(ctx.observer)


def test_runner_session_cleanup_runs_on_exception(fake_session):
    """Central architectural claim: cleanup happens even if adapter raises."""
    config, workspace = fake_session
    with (
        patch("blueclaw.runner.create_agent", return_value=_fake_agent_factory()),
        patch("blueclaw.runner.cleanup_mcp_clients") as mk_cleanup,
    ):
        captured_ctx = {}
        with pytest.raises(RuntimeError, match="adapter blew up"):
            with runner_session(config, workspace, model=MagicMock()) as ctx:
                captured_ctx["ctx"] = ctx
                raise RuntimeError("adapter blew up")
        mk_cleanup.assert_called_once_with(captured_ctx["ctx"].observer)


# ---------------------------------------------------------------------------
# finalize() tests (Task 4)
# ---------------------------------------------------------------------------

from datetime import datetime, timezone


def _fake_result(response_text: str = "hi"):
    """Stand-in for strands AgentResult — has .metrics and .message."""
    result = MagicMock(name="AgentResult")
    result.metrics.accumulated_usage = {
        "inputTokens": 10,
        "outputTokens": 5,
        "totalTokens": 15,
        "cacheReadInputTokens": 0,
        "cacheWriteInputTokens": 0,
    }
    result.message = {"role": "assistant", "content": [{"text": response_text}]}
    result.stop_reason = "end_turn"
    return result


def test_finalize_happy_path_no_capture(fake_session):
    config, workspace = fake_session
    fake_agent = _fake_agent_factory()
    fake_agent.messages = [{"role": "user", "content": "hi"}]

    with patch("blueclaw.runner.create_agent", return_value=fake_agent):
        with runner_session(config, workspace, model=MagicMock()) as ctx:
            from blueclaw.runner import finalize

            start = datetime.now(timezone.utc)
            end = datetime.now(timezone.utc)
            outcome = finalize(
                ctx,
                _fake_result("hello world"),
                goal="say hi",
                source="eval",
                conversation_id=None,
                start_time=start,
                end_time=end,
                config=config,
            )

    assert outcome.response_text == "hello world"
    assert outcome.trace is not None
    assert outcome.record is not None
    assert outcome.error is None
    assert outcome.capture_errors == []
    assert outcome.record.tokens == 15


def test_finalize_writes_capture_when_path_set(fake_session, tmp_path: Path):
    config, workspace = fake_session
    capture_path = tmp_path / "case-001" / "run-000"
    fake_agent = _fake_agent_factory()
    fake_agent.messages = [{"role": "user", "content": "hi"}]

    with patch("blueclaw.runner.create_agent", return_value=fake_agent):
        with runner_session(config, workspace, model=MagicMock()) as ctx:
            from blueclaw.runner import finalize

            now = datetime.now(timezone.utc)
            outcome = finalize(
                ctx,
                _fake_result("hello"),
                goal="g",
                source="eval",
                conversation_id=None,
                start_time=now,
                end_time=now,
                config=config,
                capture_path=capture_path,
            )

    assert (capture_path / "response.txt").read_text() == "hello"
    assert json.loads((capture_path / "messages.json").read_text()) == [
        {"role": "user", "content": "hi"}
    ]
    assert outcome.capture_errors == []


def test_finalize_default_run_id_format(fake_session):
    config, workspace = fake_session
    with patch("blueclaw.runner.create_agent", return_value=_fake_agent_factory()):
        with runner_session(config, workspace, model=MagicMock()) as ctx:
            from blueclaw.runner import finalize

            start = datetime(2026, 5, 17, 14, 30, 5, tzinfo=timezone.utc)
            outcome = finalize(
                ctx,
                _fake_result(),
                goal="g",
                source="eval",
                conversation_id=None,
                start_time=start,
                end_time=start,
                config=config,
            )

    assert outcome.trace.run_id.startswith("20260517-143005-")
    assert len(outcome.trace.run_id) == len("20260517-143005-") + 4


# ---------------------------------------------------------------------------
# finalize_error() tests (Task 5)
# ---------------------------------------------------------------------------


def test_finalize_error_populates_error_and_skips_trace(fake_session, tmp_path: Path):
    config, workspace = fake_session
    capture_path = tmp_path / "case-001" / "run-000"
    fake_agent = _fake_agent_factory()
    fake_agent.messages = [{"role": "user", "content": "boom"}]

    err = RuntimeError("model exploded")

    with patch("blueclaw.runner.create_agent", return_value=fake_agent):
        with runner_session(config, workspace, model=MagicMock()) as ctx:
            from blueclaw.runner import finalize_error

            now = datetime.now(timezone.utc)
            outcome = finalize_error(
                ctx,
                err,
                goal="g",
                source="terminal",
                conversation_id=None,
                start_time=now,
                end_time=now,
                config=config,
                capture_path=capture_path,
            )

    assert outcome.error is err
    assert outcome.trace is None
    assert outcome.record is None
    assert outcome.response_text == ""
    # Capture still happened against agent.messages at failure point.
    assert (capture_path / "response.txt").read_text() == ""
    assert json.loads((capture_path / "messages.json").read_text()) == [
        {"role": "user", "content": "boom"}
    ]
