"""Unit tests for the trace ↔ capture link feature."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from blueclaw.models import RunTrace


def _make_trace(**overrides) -> RunTrace:
    """Minimal RunTrace for round-trip tests."""
    base = dict(
        run_id="20260518-090000-abcd",
        goal="hi",
        start_time=datetime.now(timezone.utc),
        end_time=datetime.now(timezone.utc),
        model_id="anthropic/claude-opus-4-7",
        steps=[],
        total_tokens=0,
        status="success",
    )
    base.update(overrides)
    return RunTrace(**base)


class TestRunTraceCapturePath:
    def test_default_is_none(self):
        t = _make_trace()
        assert t.capture_path is None

    def test_round_trip_with_capture_path(self):
        t = _make_trace(capture_path=".blueclaw/turns/my-chat/turn-005")
        restored = RunTrace.from_json(t.to_json())
        assert restored.capture_path == ".blueclaw/turns/my-chat/turn-005"

    def test_round_trip_with_none(self):
        t = _make_trace(capture_path=None)
        restored = RunTrace.from_json(t.to_json())
        assert restored.capture_path is None


class TestRunnerRelativization:
    """finalize / finalize_error set trace.capture_path when both kwargs given."""

    def _stub_outcome(self, tmp_path, capture_path, workspace_root):
        """Drive runner.finalize with the minimum scaffolding it needs."""
        from datetime import datetime, timezone

        from blueclaw.models import SessionConfig
        from blueclaw.runner import RunnerCtx, finalize
        from blueclaw.observer import ObserverHooks
        from rich.console import Console
        import io

        observer = ObserverHooks(console=Console(file=io.StringIO()), quiet=True)
        observer.mcp_clients = []
        observer.conversation_manager = None

        class _StubAgent:
            messages = [{"role": "assistant", "content": [{"text": "ok"}]}]
            state = type("S", (), {"set": lambda self, *a, **kw: None})()

        ctx = RunnerCtx(observer=observer, agent=_StubAgent())
        result = type(
            "R",
            (),
            {
                "message": {"role": "assistant", "content": [{"text": "ok"}]},
                "metrics": type("M", (), {"accumulated_usage": {}})(),
                "stop_reason": "end_turn",
            },
        )()
        start = datetime.now(timezone.utc)
        end = datetime.now(timezone.utc)
        return finalize(
            ctx,
            result,
            goal="hi",
            source="terminal",
            conversation_id="cid",
            start_time=start,
            end_time=end,
            config=SessionConfig(),
            capture_path=capture_path,
            workspace_root=workspace_root,
        )

    def test_both_kwargs_set_relativizes(self, tmp_path):
        cp = tmp_path / ".blueclaw" / "turns" / "cid" / "turn-001"
        outcome = self._stub_outcome(tmp_path, cp, tmp_path)
        assert outcome.trace.capture_path == ".blueclaw/turns/cid/turn-001"

    def test_workspace_root_missing_leaves_none(self, tmp_path):
        cp = tmp_path / ".blueclaw" / "turns" / "cid" / "turn-001"
        outcome = self._stub_outcome(tmp_path, cp, workspace_root=None)
        assert outcome.trace.capture_path is None

    def test_capture_path_missing_leaves_none(self, tmp_path):
        outcome = self._stub_outcome(
            tmp_path, capture_path=None, workspace_root=tmp_path
        )
        assert outcome.trace.capture_path is None

    def test_capture_path_outside_workspace_raises(self, tmp_path):
        # capture_path not under workspace_root → ValueError propagates
        outside = tmp_path.parent / "elsewhere" / "turn-001"
        with pytest.raises(ValueError):
            self._stub_outcome(tmp_path, outside, workspace_root=tmp_path)
