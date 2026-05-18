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
        with self._lock:
            if self._closed:
                return
            full = {**event, "seq": self._seq, "ts": _now_iso()}
            self._seq += 1
            line = json.dumps(full, default=str) + "\n"
            try:
                self._file.write(line)
            except OSError:
                self._failed_writes += 1  # telemetry is best-effort

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
