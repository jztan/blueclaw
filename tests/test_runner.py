"""Unit tests for blueclaw/runner.py."""

from __future__ import annotations

import json
import os
from pathlib import Path

from blueclaw.runner import RunOutcome, _write_capture_artifacts


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
