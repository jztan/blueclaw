"""Unit tests for the trace ↔ capture link feature."""

from __future__ import annotations

from datetime import datetime, timezone

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
