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
