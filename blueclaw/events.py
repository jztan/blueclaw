"""Per-turn event bus — tees to disk and optional subscribers.

The bus is the single chokepoint for all observability events captured
during a turn. See
docs/superpowers/specs/2026-05-18-trace-ui-conversation-first-observability-design.md
"""

from __future__ import annotations

import json
import queue
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
SUBSCRIBER_QUEUE_SIZE = 1000


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class EventBus:
    """Thread-safe per-turn event sink.

    emit() is callable from any thread without an event loop.
    Subscribers receive events via stdlib queue.Queue — the asyncio bridge
    (used by the live broker) is the broker's responsibility, not the bus.
    """

    def __init__(self, events_path: Path) -> None:
        self._path = events_path
        events_path.parent.mkdir(parents=True, exist_ok=True)
        self._file = events_path.open("a", buffering=1, encoding="utf-8")
        self._subscriber_ids: dict[int, queue.Queue] = {}
        self._next_subscriber_id: int = 1
        self._lock = threading.Lock()
        self._seq = 0
        self._failed_writes = 0
        self._closed = False
        self._emit_schema_version()

    def _emit_schema_version(self) -> None:
        from blueclaw import __version__

        self.emit(
            {
                "type": "schema.version",
                "v": SCHEMA_VERSION,
                "blueclaw_version": __version__,
            }
        )

    def emit(self, event: dict[str, Any]) -> None:
        """Synchronous, thread-safe. Never raises."""
        # Build the framed event under lock; dispatch outside.
        dropped: list[int] = []  # subscriber_ids dropped during this dispatch
        with self._lock:
            if self._closed:
                return
            full = {**event, "seq": self._seq, "ts": _now_iso()}
            self._seq += 1
            line = json.dumps(full, default=str) + "\n"
            try:
                self._file.write(line)
            except OSError:
                self._failed_writes += 1
            subscribers_snapshot = list(self._subscriber_ids.items())

        # Dispatch outside the file lock so a slow subscriber can't block writes.
        for sub_id, q in subscribers_snapshot:
            try:
                q.put_nowait(full)
            except queue.Full:
                self._unregister_subscriber(sub_id)
                dropped.append(sub_id)

        # Emit one stream.dropped per dropped subscriber AFTER fan-out finishes.
        # Avoids re-entering emit from within the dispatch loop (cascading recursion).
        for sub_id in dropped:
            self._emit_drop_notice(sub_id)

    def subscribe(self) -> queue.Queue:
        """Register a subscriber. Returns a stdlib queue.Queue with bounded capacity."""
        q: queue.Queue = queue.Queue(maxsize=SUBSCRIBER_QUEUE_SIZE)
        with self._lock:
            sub_id = self._next_subscriber_id
            self._next_subscriber_id += 1
            self._subscriber_ids[sub_id] = q
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            for sub_id, registered_q in list(self._subscriber_ids.items()):
                if registered_q is q:
                    del self._subscriber_ids[sub_id]
                    return

    def _unregister_subscriber(self, sub_id: int) -> None:
        with self._lock:
            self._subscriber_ids.pop(sub_id, None)

    def _emit_drop_notice(self, sub_id: int) -> None:
        """Emit a stream.dropped notice. Goes through the normal emit path,
        which is safe now because the dropped subscriber is already unregistered
        and we're outside the per-event dispatch loop."""
        self.emit({"type": "stream.dropped", "subscriber_id": sub_id})

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                self._file.flush()
                self._file.close()
            except OSError:
                pass

    @property
    def failed_writes(self) -> int:
        with self._lock:
            return self._failed_writes
