"""Unix-socket fan-out broker for live event streaming.

A running ``blueclaw`` process (the *producer*) connects to the broker's Unix
domain socket and streams newline-delimited JSON events.  A separate
``blueclaw trace ui --live`` process (the *consumer*) subscribes via
:meth:`LiveBroker.subscribe` and receives those events through a stdlib
``queue.Queue`` so that the asyncio-based SSE handler never needs to share an
event loop with the broker.

Wire protocol (producer side):
    1. Connect to the Unix socket.
    2. Send a one-line JSON handshake::

           {"type": "bus.register", "cid": "<cid>", "run_id": "<run_id>"}\\n

    3. Send subsequent newline-delimited JSON event lines (verbatim from the
       EventBus, i.e. already carrying ``seq`` and ``ts`` fields).
    4. Close the connection when the turn ends.  The broker synthesises a
       ``{"type": "stream.end", "cid": ..., "run_id": ...}`` sentinel for every
       subscriber of that cid.

Threading model:
    The broker owns a single background thread that runs ``asyncio.run()``.
    All async coroutines live inside that thread.  ``subscribe`` / ``unsubscribe``
    use a plain ``threading.Lock`` so callers on any thread stay safe.
"""

from __future__ import annotations

import asyncio
import atexit
import json
import logging
import os
import queue
import signal
import threading
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_SOCK_MODE = 0o600
_LOCK_MODE = 0o600
_QUEUE_SIZE = 1000


class LiveBroker:
    """Process-singleton broker for live event streaming over a Unix socket.

    Lifecycle::

        broker = LiveBroker()
        broker.start()                    # acquire lock, open socket
        q = broker.subscribe("cid-abc")   # SSE handler calls this
        broker.unsubscribe("cid-abc", q)  # SSE handler done
        broker.stop()                     # idempotent cleanup
    """

    DEFAULT_SOCK_PATH: Path = Path.home() / ".blueclaw" / "live.sock"
    DEFAULT_LOCK_PATH: Path = Path.home() / ".blueclaw" / "live.lock"

    def __init__(
        self,
        sock_path: Path = DEFAULT_SOCK_PATH,
        lock_path: Path = DEFAULT_LOCK_PATH,
    ) -> None:
        self._sock_path = sock_path
        self._lock_path = lock_path

        # subscribers: cid -> set of queues
        self._subscribers: dict[str, set[queue.Queue]] = {}
        self._sub_lock = threading.Lock()

        # asyncio / threading internals
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._server: Optional[asyncio.Server] = None
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._stopped = False
        self._stop_event: Optional[asyncio.Event] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Acquire the lock file and open the Unix socket.

        If a stale lock exists (dead PID), it is silently removed.
        If the lock is held by a live process *other than this one*, raises
        ``RuntimeError``.
        """
        self._acquire_lock()
        self._thread = threading.Thread(
            target=self._run_loop, name="live-broker", daemon=True
        )
        self._thread.start()
        # Block until the asyncio server is fully up (or the thread dies).
        self._ready.wait(timeout=5.0)
        if not self._ready.is_set():
            raise RuntimeError("LiveBroker failed to start within 5 s")

        atexit.register(self.stop)
        # Best-effort SIGTERM / SIGINT hooks (only on the main thread).
        try:
            signal.signal(signal.SIGTERM, self._handle_signal)
            signal.signal(signal.SIGINT, self._handle_signal)
        except ValueError:
            # Not on the main thread — skip signal registration.
            pass

    def stop(self) -> None:
        """Idempotent shutdown: close socket, unlink lock + socket files."""
        if self._stopped:
            return
        self._stopped = True

        # Ask the asyncio loop to stop cleanly.
        if self._loop is not None and self._stop_event is not None:
            self._loop.call_soon_threadsafe(self._stop_event.set)

        if self._thread is not None:
            self._thread.join(timeout=3.0)

        # Unlink files regardless of whether the thread finished cleanly.
        for path in (self._sock_path, self._lock_path):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                logger.debug("LiveBroker.stop: could not unlink %s: %s", path, exc)

    def subscribe(self, cid: str) -> queue.Queue:
        """Return a new ``queue.Queue`` that will receive events for *cid*.

        The queue has a bounded capacity of 1 000 items.  If the producer
        outpaces the consumer the item is dropped (the ``put_nowait`` call
        silently discards it).
        """
        q: queue.Queue = queue.Queue(maxsize=_QUEUE_SIZE)
        with self._sub_lock:
            self._subscribers.setdefault(cid, set()).add(q)
        return q

    def unsubscribe(self, cid: str, q: queue.Queue) -> None:
        """Remove *q* from the subscriber set for *cid*."""
        with self._sub_lock:
            queues = self._subscribers.get(cid)
            if queues:
                queues.discard(q)
                if not queues:
                    del self._subscribers[cid]

    # ------------------------------------------------------------------
    # Lock management
    # ------------------------------------------------------------------

    def _acquire_lock(self) -> None:
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        my_pid = os.getpid()

        while True:
            # Try atomic create.
            try:
                fd = os.open(
                    str(self._lock_path),
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    _LOCK_MODE,
                )
                os.write(fd, str(my_pid).encode())
                os.close(fd)
                # Ensure the mode is set even if umask narrowed it.
                try:
                    os.chmod(str(self._lock_path), _LOCK_MODE)
                except OSError:
                    pass
                return
            except FileExistsError:
                pass  # lock already exists — inspect it

            # Read the existing PID.
            try:
                existing_pid = int(self._lock_path.read_text().strip())
            except (OSError, ValueError):
                # Unreadable / corrupt — treat as stale.
                try:
                    self._lock_path.unlink()
                except OSError:
                    pass
                continue

            # Check liveness.
            try:
                os.kill(existing_pid, 0)
                # Signal 0 succeeded — process is alive.
                raise RuntimeError(f"another LiveBroker is running, pid={existing_pid}")
            except ProcessLookupError:
                # Dead PID — stale lock; unlink and retry.
                try:
                    self._lock_path.unlink()
                except OSError:
                    pass
                continue
            except PermissionError:
                # Process exists but is owned by another user — treat as alive.
                raise RuntimeError(f"another LiveBroker is running, pid={existing_pid}")

    # ------------------------------------------------------------------
    # Asyncio server (runs inside the background thread)
    # ------------------------------------------------------------------

    def _run_loop(self) -> None:
        asyncio.run(self._serve())

    async def _serve(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._stop_event = asyncio.Event()

        # Remove any stale socket file before binding.
        try:
            self._sock_path.unlink()
        except FileNotFoundError:
            pass

        self._sock_path.parent.mkdir(parents=True, exist_ok=True)

        # Unix socket paths are limited to ~104 bytes on macOS and ~108 on
        # Linux.  Long pytest tmp_path values can exceed this.  Bind using
        # just the filename after chdir-ing to the parent directory so the
        # kernel sees only a short path.  We restore the cwd afterwards.
        sock_dir = str(self._sock_path.parent.resolve())
        sock_name = self._sock_path.name
        saved_cwd = os.getcwd()
        try:
            os.chdir(sock_dir)
            bind_path = sock_name  # short relative path
        except OSError:
            # If chdir fails, fall back to the full path and hope it fits.
            bind_path = str(self._sock_path)
            saved_cwd = None  # nothing to restore

        try:
            server = await asyncio.start_unix_server(
                self._handle_client,
                path=bind_path,
            )
        finally:
            if saved_cwd is not None:
                try:
                    os.chdir(saved_cwd)
                except OSError:
                    pass
        # Tighten permissions to 0600.
        try:
            os.chmod(str(self._sock_path), _SOCK_MODE)
        except OSError as exc:
            logger.debug("LiveBroker: could not chmod socket: %s", exc)

        self._server = server

        async with server:
            self._ready.set()
            await self._stop_event.wait()

        # Server context manager closed; fall through to clean up.

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        cid: Optional[str] = None
        run_id: Optional[str] = None

        try:
            # --- Handshake ---
            handshake_line = await reader.readline()
            if not handshake_line:
                return
            try:
                handshake = json.loads(handshake_line.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                logger.debug("LiveBroker: invalid handshake JSON, closing")
                return

            if handshake.get("type") != "bus.register":
                logger.debug("LiveBroker: unexpected handshake type, closing")
                return

            cid = handshake.get("cid", "")
            run_id = handshake.get("run_id", "")

            # --- Event stream ---
            while True:
                line = await reader.readline()
                if not line:
                    # EOF — producer closed connection.
                    break
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    event = json.loads(stripped.decode("utf-8", errors="replace"))
                except json.JSONDecodeError:
                    logger.debug("LiveBroker: skipping non-JSON line from producer")
                    continue

                self._fan_out(cid, event)

        except (asyncio.IncompleteReadError, ConnectionResetError, OSError):
            pass
        finally:
            if cid is not None:
                # Synthesise end-of-turn sentinel for all subscribers.
                sentinel = {
                    "type": "stream.end",
                    "cid": cid,
                    "run_id": run_id,
                }
                self._fan_out(cid, sentinel)
            try:
                writer.close()
                await writer.wait_closed()
            except OSError:
                pass

    def _fan_out(self, cid: str, event: dict) -> None:
        """Deliver *event* to every subscriber of *cid* (drop on overflow)."""
        with self._sub_lock:
            queues = list(self._subscribers.get(cid, set()))
        for q in queues:
            try:
                q.put_nowait(event)
            except queue.Full:
                logger.debug(
                    "LiveBroker: subscriber queue full for cid=%s, dropping event",
                    cid,
                )

    # ------------------------------------------------------------------
    # Signal helpers
    # ------------------------------------------------------------------

    def _handle_signal(self, signum: int, frame) -> None:  # noqa: ANN001
        self.stop()
