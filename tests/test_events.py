"""Unit tests for EventBus."""

from __future__ import annotations

import json
import threading
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


def test_seq_is_monotonic_and_starts_at_zero(tmp_path: Path) -> None:
    bus = EventBus(tmp_path / "events.jsonl")
    for i in range(5):
        bus.emit({"type": "tool.before", "tool_name": f"t{i}"})
    bus.close()

    raw = (tmp_path / "events.jsonl").read_text().splitlines()
    seqs = [json.loads(line)["seq"] for line in raw]
    # schema.version=0, then 1..5
    assert seqs == list(range(6))


def test_seq_is_thread_safe(tmp_path: Path) -> None:
    bus = EventBus(tmp_path / "events.jsonl")

    def producer(n: int) -> None:
        for i in range(n):
            bus.emit({"type": "tool.before", "tool_name": "t", "i": i})

    threads = [threading.Thread(target=producer, args=(100,)) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    bus.close()

    raw = (tmp_path / "events.jsonl").read_text().splitlines()
    seqs = [json.loads(line)["seq"] for line in raw]
    # 1 schema.version + 10 threads * 100 emits = 1001
    assert len(seqs) == 1001
    # Every seq from 0..1000 exactly once
    assert sorted(seqs) == list(range(1001))


def test_disk_failure_is_swallowed(tmp_path: Path, monkeypatch) -> None:
    bus = EventBus(tmp_path / "events.jsonl")

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(bus._file, "write", boom)
    # Must not raise
    bus.emit({"type": "tool.before", "tool_name": "x"})
    assert bus.failed_writes >= 1
    bus.close()


def test_emit_after_close_is_no_op(tmp_path: Path) -> None:
    bus = EventBus(tmp_path / "events.jsonl")
    bus.close()
    bus.emit({"type": "tool.before", "tool_name": "x"})  # must not raise
    lines = (tmp_path / "events.jsonl").read_text().splitlines()
    # Only the schema.version line — no post-close emit
    assert len(lines) == 1
