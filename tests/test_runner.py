"""Unit tests for blueclaw/runner.py."""

from __future__ import annotations

from blueclaw.runner import RunOutcome


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
