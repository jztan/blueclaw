"""Per-turn event bus — tees to disk and optional subscribers.

The bus is the single chokepoint for all observability events captured
during a turn. See
docs/superpowers/specs/2026-05-18-trace-ui-conversation-first-observability-design.md
"""

from __future__ import annotations

import json
import logging
import os
import queue
import socket
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
SUBSCRIBER_QUEUE_SIZE = 1000

_DEFAULT_SOCK_PATH = Path.home() / ".blueclaw" / "live.sock"
_DEFAULT_LOCK_PATH = Path.home() / ".blueclaw" / "live.lock"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _try_connect_live_broker(
    sock_path: Path,
    lock_path: Path,
    cid: str,
    run_id: str | None,
) -> "socket.socket | None":
    """Attempt to connect to a running LiveBroker and send the handshake.

    Pure in intent: no side effects beyond opening the socket.
    Returns the connected socket on success, None on any failure.
    """
    try:
        # Fast stat check — no lock file means no broker.
        if not lock_path.exists():
            return None

        # Read PID and send signal 0 to verify liveness.
        try:
            pid = int(lock_path.read_text().strip())
        except (OSError, ValueError):
            return None

        try:
            os.kill(pid, 0)
        except (ProcessLookupError, PermissionError, OSError):
            return None

        # Connect via AF_UNIX. Use a relative path from the socket's parent
        # directory to stay within the kernel's ~104-byte path limit on macOS.
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(1.0)

        saved_cwd = os.getcwd()
        try:
            os.chdir(str(sock_path.parent.resolve()))
            sock.connect(sock_path.name)
        finally:
            try:
                os.chdir(saved_cwd)
            except OSError:
                pass

        # Send handshake.
        handshake: dict[str, Any] = {"type": "bus.register", "cid": cid}
        if run_id is not None:
            handshake["run_id"] = run_id
        sock.sendall((json.dumps(handshake) + "\n").encode("utf-8"))

        sock.settimeout(None)  # back to blocking for subsequent writes
        return sock
    except Exception as exc:
        logger.debug("EventBus: live broker connect failed: %s", exc)
        return None


class EventBus:
    """Thread-safe per-turn event sink.

    emit() is callable from any thread without an event loop.
    Subscribers receive events via stdlib queue.Queue — the asyncio bridge
    (used by the live broker) is the broker's responsibility, not the bus.

    When cid and run_id are provided and a LiveBroker is running, every
    emitted event is also forwarded to the broker over a Unix socket.
    Any socket failure closes the connection and falls back to disk-only.
    """

    def __init__(
        self,
        events_path: Path,
        *,
        cid: str | None = None,
        run_id: str | None = None,
        live_sock_path: Path | None = None,
        live_lock_path: Path | None = None,
    ) -> None:
        self._path = events_path
        events_path.parent.mkdir(parents=True, exist_ok=True)
        self._file = events_path.open("a", buffering=1, encoding="utf-8")
        self._subscriber_ids: dict[int, queue.Queue] = {}
        self._next_subscriber_id: int = 1
        self._lock = threading.Lock()
        self._seq = 0
        self._failed_writes = 0
        self._closed = False

        # Live broker socket — None means disk-only mode.
        self._live_client: socket.socket | None = None
        if cid is not None:
            sock_path = (
                live_sock_path if live_sock_path is not None else _DEFAULT_SOCK_PATH
            )
            lock_path = (
                live_lock_path if live_lock_path is not None else _DEFAULT_LOCK_PATH
            )
            try:
                self._live_client = _try_connect_live_broker(
                    sock_path, lock_path, cid, run_id
                )
            except Exception as exc:
                logger.debug("EventBus: unexpected error connecting to broker: %s", exc)
                self._live_client = None

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
        live_line: str | None = None
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
            if self._live_client is not None:
                live_line = line
            subscribers_snapshot = list(self._subscriber_ids.items())

        # Forward to live broker outside the file lock.
        if live_line is not None:
            try:
                self._live_client.sendall(  # type: ignore[union-attr]
                    live_line.encode("utf-8")
                )
            except Exception as exc:
                logger.debug("EventBus: live broker write failed, closing: %s", exc)
                with self._lock:
                    try:
                        self._live_client.close()  # type: ignore[union-attr]
                    except Exception:
                        pass
                    self._live_client = None

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
        self.emit(
            {
                "type": "stream.dropped",
                "subscriber_id": sub_id,
                "dropped_count": 1,
            }
        )

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
            if self._live_client is not None:
                try:
                    self._live_client.close()
                except Exception:
                    pass
                self._live_client = None

    @property
    def failed_writes(self) -> int:
        with self._lock:
            return self._failed_writes
