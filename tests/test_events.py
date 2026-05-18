"""Unit tests for EventBus."""

from __future__ import annotations

import json
from pathlib import Path

from blueclaw.events import EventBus


def test_bus_writes_schema_version_as_first_line(tmp_path: Path) -> None:
    events_path = tmp_path / "events.jsonl"
    bus = EventBus(events_path)
    bus.close()

    lines = events_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    first = json.loads(lines[0])
    assert first["type"] == "schema.version"
    assert first["v"] == 1
    assert first["seq"] == 0
    assert "ts" in first
    assert "blueclaw_version" in first


def test_close_is_idempotent(tmp_path: Path) -> None:
    bus = EventBus(tmp_path / "events.jsonl")
    bus.close()
    bus.close()  # must not raise


def test_caller_cannot_override_seq_or_ts(tmp_path: Path) -> None:
    """Bus-controlled seq and ts must win over caller-supplied keys."""
    bus = EventBus(tmp_path / "events.jsonl")
    # Caller tries to inject seq=99 and ts="bogus" — must be ignored.
    bus.emit({"type": "tool.before", "seq": 99, "ts": "bogus", "tool_name": "x"})
    bus.close()

    lines = (tmp_path / "events.jsonl").read_text().splitlines()
    # schema.version (seq=0), then the emit above (seq=1)
    assert len(lines) == 2
    second = json.loads(lines[1])
    assert second["seq"] == 1  # bus-controlled, NOT the caller's 99
    assert second["ts"] != "bogus"  # bus-controlled ISO timestamp
    assert second["type"] == "tool.before"
    assert second["tool_name"] == "x"  # caller payload preserved
