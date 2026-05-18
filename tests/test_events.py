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


import queue as _q


def test_subscriber_receives_events(tmp_path: Path) -> None:
    bus = EventBus(tmp_path / "events.jsonl")
    q = bus.subscribe()
    bus.emit({"type": "tool.before", "tool_name": "x"})
    bus.emit({"type": "tool.after", "tool_name": "x"})
    bus.close()

    events = []
    while not q.empty():
        events.append(q.get_nowait())
    # schema.version was emitted BEFORE subscribe — only post-subscribe events arrive
    types = [e["type"] for e in events]
    assert types == ["tool.before", "tool.after"]


def test_unsubscribe_stops_delivery(tmp_path: Path) -> None:
    bus = EventBus(tmp_path / "events.jsonl")
    q = bus.subscribe()
    bus.unsubscribe(q)
    bus.emit({"type": "tool.before", "tool_name": "x"})
    bus.close()
    assert q.empty()


def test_overflow_drops_subscriber_and_emits_stream_dropped(tmp_path: Path) -> None:
    bus = EventBus(tmp_path / "events.jsonl")
    slow = bus.subscribe()  # noqa: F841 — held to keep the queue alive
    fast = bus.subscribe()

    from blueclaw.events import SUBSCRIBER_QUEUE_SIZE

    drain_thread_stop = [False]  # mutable container so the inner closure can see it

    def drain_fast() -> None:
        while not drain_thread_stop[0]:
            try:
                fast.get(timeout=0.01)
            except _q.Empty:
                pass

    t = threading.Thread(target=drain_fast)
    t.start()
    try:
        for i in range(SUBSCRIBER_QUEUE_SIZE + 5):
            bus.emit({"type": "tool.before", "tool_name": "spam", "i": i})
    finally:
        drain_thread_stop[0] = True
        t.join(timeout=1)
    bus.close()

    lines = (tmp_path / "events.jsonl").read_text().splitlines()
    types = [json.loads(line)["type"] for line in lines]
    assert "stream.dropped" in types

    drop_events = [
        json.loads(line)
        for line in lines
        if json.loads(line)["type"] == "stream.dropped"
    ]
    for ev in drop_events:
        assert isinstance(ev["subscriber_id"], int)
        # IDs are small monotonic counter values, not 14-digit memory addresses.
        assert ev["subscriber_id"] < 10_000
        assert ev["dropped_count"] == 1


def test_bus_connects_to_live_broker(tmp_path: Path) -> None:
    """When a LiveBroker is running, EventBus forwards events to it."""
    import time

    from blueclaw.events import EventBus
    from blueclaw.live_broker import LiveBroker

    sock_path = tmp_path / "live.sock"
    lock_path = tmp_path / "live.lock"
    broker = LiveBroker(sock_path=sock_path, lock_path=lock_path)
    broker.start()
    try:
        # Wait for broker to come up
        deadline = time.time() + 2.0
        while not sock_path.exists() and time.time() < deadline:
            time.sleep(0.01)
        assert sock_path.exists()

        q = broker.subscribe("cid-live")

        bus = EventBus(
            tmp_path / "events.jsonl",
            cid="cid-live",
            run_id="r1",
            live_sock_path=sock_path,
            live_lock_path=lock_path,
        )
        bus.emit({"type": "tool.before", "tool_name": "x"})
        bus.close()

        # Drain queue
        received = []
        deadline = time.time() + 1.0
        while time.time() < deadline and len(received) < 3:
            try:
                received.append(q.get(timeout=0.1))
            except Exception:
                pass
        types = [e["type"] for e in received]
        # Should include the schema.version, the tool.before, and stream.end
        assert "tool.before" in types
        # Disk also wrote (independent of broker)
        lines = (tmp_path / "events.jsonl").read_text().splitlines()
        assert len(lines) >= 2  # schema.version + tool.before
    finally:
        broker.stop()


def test_bus_works_without_broker(tmp_path: Path) -> None:
    """No broker running → bus writes to disk only, no exception."""
    bus = EventBus(
        tmp_path / "events.jsonl",
        cid="cid-x",
        run_id="r1",
        live_sock_path=tmp_path / "nonexistent.sock",
        live_lock_path=tmp_path / "nonexistent.lock",
    )
    bus.emit({"type": "tool.before", "tool_name": "x"})
    bus.close()
    # No exception. Disk write succeeded.
    lines = (tmp_path / "events.jsonl").read_text().splitlines()
    assert len(lines) >= 2


def test_bus_without_cid_skips_live_connection(tmp_path: Path) -> None:
    """cid=None means we don't even attempt to connect."""
    import time

    from blueclaw.live_broker import LiveBroker

    sock = tmp_path / "live.sock"
    lock = tmp_path / "live.lock"
    broker = LiveBroker(sock_path=sock, lock_path=lock)
    broker.start()
    try:
        deadline = time.time() + 2.0
        while not sock.exists() and time.time() < deadline:
            time.sleep(0.01)

        bus = EventBus(
            tmp_path / "events.jsonl",
            cid=None,
            run_id=None,
            live_sock_path=sock,
            live_lock_path=lock,
        )
        # Connection attempted? Should be False.
        assert bus._live_client is None
        bus.close()
    finally:
        broker.stop()


def test_no_recursive_drop_under_cascading_overflow(tmp_path: Path) -> None:
    """If multiple subscribers overflow during a single emit, dispatch must
    not recurse — stream.dropped notices fire after dispatch finishes."""
    bus = EventBus(tmp_path / "events.jsonl")
    # Create three subscribers, none draining. All will overflow at the same emit.
    [bus.subscribe() for _ in range(3)]

    from blueclaw.events import SUBSCRIBER_QUEUE_SIZE

    # Fill all three queues, then one more emit to trigger triple-overflow.
    for i in range(SUBSCRIBER_QUEUE_SIZE):
        bus.emit({"type": "tool.before", "tool_name": "x", "i": i})
    bus.emit({"type": "tool.before", "tool_name": "trigger"})
    bus.close()

    lines = (tmp_path / "events.jsonl").read_text().splitlines()
    drops = [
        json.loads(line)
        for line in lines
        if json.loads(line)["type"] == "stream.dropped"
    ]
    assert len(drops) == 3
    assert len({d["subscriber_id"] for d in drops}) == 3
