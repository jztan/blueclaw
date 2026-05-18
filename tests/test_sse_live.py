"""Tests for the SSE live-events endpoint."""

from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from blueclaw.live_broker import LiveBroker
from blueclaw.web import create_app
from blueclaw.workspace import Workspace


@pytest.fixture
def broker_paths(tmp_path: Path):
    return tmp_path / "live.sock", tmp_path / "live.lock"


@pytest.fixture
def running_broker(broker_paths):
    sock, lock = broker_paths
    b = LiveBroker(sock_path=sock, lock_path=lock)
    b.start()
    # Wait
    deadline = time.time() + 2.0
    while not sock.exists() and time.time() < deadline:
        time.sleep(0.01)
    yield b
    b.stop()


@pytest.fixture
def workspace(tmp_path):
    ws = Workspace(tmp_path / "ws")
    ws.root.mkdir(parents=True, exist_ok=True)
    return ws


def _parse_sse(text: str) -> list[tuple[str, dict]]:
    """Parse SSE event:data pairs out of a captured stream chunk."""
    out = []
    current_event = None
    for line in text.splitlines():
        if line.startswith("event: "):
            current_event = line[len("event: ") :]
        elif line.startswith("data: ") and current_event is not None:
            payload = json.loads(line[len("data: ") :])
            out.append((current_event, payload))
            current_event = None
    return out


def test_live_endpoint_503_when_no_broker(workspace):
    app = create_app([("workspace", workspace)])  # no broker
    client = TestClient(app)
    # Create a fake events.jsonl so the path exists
    cid = "cid-x"
    cap = workspace.root / ".blueclaw" / "conversations" / cid / "turns" / "turn-001"
    cap.mkdir(parents=True, exist_ok=True)
    (cap / "events.jsonl").write_text('{"seq":0,"type":"schema.version"}\n')
    resp = client.get(f"/api/conversations/{cid}/turns/1/events/live")
    assert resp.status_code == 503


def test_live_endpoint_streams_backfill_and_append(
    running_broker, broker_paths, workspace
):
    sock, lock = broker_paths
    cid = "cid-live"
    # Pre-create events.jsonl with two events (backfill content)
    cap = workspace.root / ".blueclaw" / "conversations" / cid / "turns" / "turn-001"
    cap.mkdir(parents=True, exist_ok=True)
    (cap / "events.jsonl").write_text(
        '{"seq":0,"type":"schema.version"}\n'
        '{"seq":1,"type":"tool.before","tool_name":"x"}\n'
    )

    app = create_app([("workspace", workspace)], live_broker=running_broker)
    client = TestClient(app)

    # Spawn a producer thread that simulates a running blueclaw process.
    # Connect via chdir+relative path to avoid AF_UNIX path-length limit
    # (macOS cap is 104 bytes; pytest tmp_path can exceed it).
    def producer():
        import os

        time.sleep(0.3)  # give SSE handler time to subscribe + backfill
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        saved_cwd = os.getcwd()
        try:
            os.chdir(str(sock.parent))
            s.connect(sock.name)
        finally:
            os.chdir(saved_cwd)
        handshake = json.dumps({"type": "bus.register", "cid": cid, "run_id": "r1"})
        s.sendall((handshake + "\n").encode())
        live_evt = json.dumps({"seq": 2, "type": "tool.after", "status": "success"})
        s.sendall((live_evt + "\n").encode())
        time.sleep(0.2)
        s.close()  # broker emits stream.end

    t = threading.Thread(target=producer)
    t.start()

    with client.stream("GET", f"/api/conversations/{cid}/turns/1/events/live") as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        # Read up to a reasonable byte cap or until "end" event
        body_chunks = []
        for chunk in resp.iter_text():
            body_chunks.append(chunk)
            if "event: end" in "".join(body_chunks):
                break
            if sum(len(c) for c in body_chunks) > 100_000:
                break
        body = "".join(body_chunks)
    t.join(timeout=2)

    events = _parse_sse(body)
    kinds = [e[0] for e in events]
    assert "backfill" in kinds
    assert "append" in kinds
    assert "end" in kinds

    # Backfill should have last_seq=1 (since the file had seq 0 and 1)
    backfill = next(e for k, e in events if k == "backfill")
    assert backfill["last_seq"] == 1
    assert len(backfill["events"]) == 2

    # Append should be the seq=2 event (not the seq=1 from backfill, dedup'd)
    appends = [e for k, e in events if k == "append"]
    assert any(a.get("seq") == 2 for a in appends)
    assert not any(a.get("seq") == 1 for a in appends)  # dedup'd


def test_live_endpoint_invalid_cid(workspace, running_broker):
    app = create_app([("workspace", workspace)], live_broker=running_broker)
    client = TestClient(app)
    resp = client.get("/api/conversations/..%2F/turns/1/events/live")
    assert resp.status_code in (400, 404)


def test_live_endpoint_invalid_n(workspace, running_broker):
    app = create_app([("workspace", workspace)], live_broker=running_broker)
    client = TestClient(app)
    resp = client.get("/api/conversations/A/turns/0/events/live")
    assert resp.status_code == 400
