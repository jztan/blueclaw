"""Per-invocation cooperative cancellation and owned shell processes."""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path
from typing import Any


class CancellationControl:
    def __init__(self) -> None:
        self.event = threading.Event()
        self._lock = threading.Lock()
        self._reason: str | None = None
        self._agent: Any = None
        self._finalizing = False
        self._processes: set[subprocess.Popen] = set()

    @property
    def reason(self) -> str | None:
        with self._lock:
            return self._reason

    def request_stop(self, reason: str) -> bool:
        with self._lock:
            if self._finalizing:
                return False
            first = self._reason is None
            if first:
                self._reason = reason
                self.event.set()
            agent = self._agent if first else None
        if agent is not None:
            agent.cancel()
        return True

    def attach(self, agent: Any) -> None:
        with self._lock:
            self._agent = agent
            stopped = self.event.is_set()
        if stopped:
            agent.cancel()

    def begin_finalization(self) -> str | None:
        with self._lock:
            self._finalizing = True
            return self._reason

    def spawn(self, command: str, cwd: Path) -> subprocess.Popen | None:
        with self._lock:
            if self.event.is_set():
                return None
            process = subprocess.Popen(
                command,
                shell=True,
                cwd=str(cwd),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
            self._processes.add(process)
            return process

    def release(self, process: subprocess.Popen) -> None:
        with self._lock:
            self._processes.discard(process)
