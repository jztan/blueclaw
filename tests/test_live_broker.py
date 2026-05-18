"""Tests for the Unix-socket LiveBroker."""

from __future__ import annotations

import json
import os
import queue
import socket
import time
from pathlib import Path

import pytest

from blueclaw.live_broker import LiveBroker


@pytest.fixture
def broker_paths(tmp_path: Path):
    sock = tmp_path / "live.sock"
    lock = tmp_path / "live.lock"
    return sock, lock


@pytest.fixture
def broker(broker_paths):
    sock, lock = broker_paths
    b = LiveBroker(sock_path=sock, lock_path=lock)
    b.start()
    # Wait for the socket file to appear
    deadline = time.time() + 2.0
    while not sock.exists() and time.time() < deadline:
        time.sleep(0.01)
    assert sock.exists(), "broker socket did not appear"
    yield b
    b.stop()


def _connect_and_send(sock_path: Path, lines: list[str]) -> None:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    # Unix socket paths are limited to ~104 bytes on macOS / ~108 on Linux.
    # pytest tmp_path values can exceed this.  Connect via a relative path
    # from the socket's parent directory to stay within the kernel limit.
    saved_cwd = os.getcwd()
    try:
        os.chdir(str(sock_path.parent))
        s.connect(sock_path.name)
    finally:
        os.chdir(saved_cwd)
    for line in lines:
        s.sendall((line + "\n").encode("utf-8"))
    s.close()


def test_broker_creates_lock_and_socket(broker, broker_paths):
    sock, lock = broker_paths
    assert sock.exists()
    assert lock.exists()
    pid_in_lock = int(lock.read_text().strip())
    assert pid_in_lock == os.getpid()


def test_broker_dispatches_to_subscribers(broker, broker_paths):
    sock, _ = broker_paths
    q = broker.subscribe("cid-a")

    _connect_and_send(
        sock,
        [
            json.dumps({"type": "bus.register", "cid": "cid-a", "run_id": "r1"}),
            json.dumps({"seq": 1, "type": "tool.before", "tool_name": "x"}),
            json.dumps({"seq": 2, "type": "tool.after", "status": "success"}),
        ],
    )

    # Wait briefly for dispatch
    received = []
    deadline = time.time() + 1.5
    while time.time() < deadline and len(received) < 3:
        try:
            received.append(q.get(timeout=0.1))
        except queue.Empty:
            pass

    types = [e["type"] for e in received]
    assert "tool.before" in types
    assert "tool.after" in types
    # stream.end fires when producer disconnects
    assert "stream.end" in types


def test_broker_cid_isolation(broker, broker_paths):
    sock, _ = broker_paths
    q_a = broker.subscribe("cid-a")
    q_b = broker.subscribe("cid-b")
    _connect_and_send(
        sock,
        [
            json.dumps({"type": "bus.register", "cid": "cid-a", "run_id": "r1"}),
            json.dumps({"seq": 1, "type": "tool.before", "tool_name": "x"}),
        ],
    )
    time.sleep(0.3)
    # q_a should have at least one event (plus stream.end)
    a_count = 0
    while True:
        try:
            q_a.get_nowait()
            a_count += 1
        except queue.Empty:
            break
    assert a_count >= 1
    # q_b should have nothing
    with pytest.raises(queue.Empty):
        q_b.get_nowait()


def test_broker_stale_lock_recovers(tmp_path: Path):
    sock = tmp_path / "live.sock"
    lock = tmp_path / "live.lock"
    # Write a stale PID into the lock (PID 1 is init — alive but not us;
    # use 999999999 which definitely doesn't exist).
    lock.write_text("999999999")

    b = LiveBroker(sock_path=sock, lock_path=lock)
    b.start()
    try:
        deadline = time.time() + 2.0
        while not sock.exists() and time.time() < deadline:
            time.sleep(0.01)
        assert sock.exists()
        # Lock file now has OUR pid
        assert int(lock.read_text().strip()) == os.getpid()
    finally:
        b.stop()


def test_broker_stop_is_idempotent(broker):
    broker.stop()
    broker.stop()  # must not raise


def test_broker_refuses_to_start_when_alive_holder_exists(tmp_path: Path):
    sock = tmp_path / "live.sock"
    lock = tmp_path / "live.lock"
    # Real PID held by this test process
    lock.write_text(str(os.getpid()))

    b = LiveBroker(sock_path=sock, lock_path=lock)
    with pytest.raises(RuntimeError):
        b.start()
