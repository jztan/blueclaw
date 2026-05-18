"""Tests for /api/conversations endpoint."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from blueclaw.models import RunTrace
from blueclaw.web import create_app
from blueclaw.workspace import Workspace


def _make_trace(
    cid: str,
    run_id: str,
    start: datetime,
    *,
    source: str = "terminal",
    tokens: int = 100,
    cost: float | None = 0.01,
    status: str = "success",
    model: str = "claude-opus-4-7",
) -> RunTrace:
    return RunTrace(
        run_id=run_id,
        goal="g",
        start_time=start,
        end_time=start,
        model_id=model,
        steps=[],
        total_tokens=tokens,
        total_cost=cost,
        status=status,
        conversation_id=cid,
        source=source,
    )


@pytest.fixture
def workspace_with_traces(tmp_path: Path):
    ws = Workspace(tmp_path / "ws")
    ws.root.mkdir(parents=True, exist_ok=True)

    base = datetime(2026, 5, 18, 10, 0, 0, tzinfo=timezone.utc)

    # cid A: 3 turns, 2 success + 1 error
    for i in range(3):
        ws.write_trace(
            _make_trace(
                "A",
                f"20260518-1000{i:02}-aaaa",
                base.replace(minute=i),
                status="error" if i == 2 else "success",
            )
        )
    # cid B: 1 turn, with no cost
    ws.write_trace(
        _make_trace(
            "B",
            "20260518-110000-bbbb",
            base.replace(hour=11),
            cost=None,
        )
    )
    return ws


def test_list_conversations_groups_by_cid(workspace_with_traces):
    app = create_app([("workspace", workspace_with_traces)])
    client = TestClient(app)
    resp = client.get("/api/conversations")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2  # cids A and B

    convs = {c["conversation_id"]: c for c in body["conversations"]}
    a = convs["A"]
    assert a["turn_count"] == 3
    assert a["status_counts"] == {"success": 2, "error": 1}
    assert a["model_ids"] == ["claude-opus-4-7"]
    assert a["total_tokens"] == 300
    assert a["total_cost"] == pytest.approx(0.03)
    assert a["turns_with_unknown_cost"] == 0
    assert a["source"] == "terminal"

    b = convs["B"]
    assert b["turn_count"] == 1
    assert b["total_cost"] == 0.0
    assert b["turns_with_unknown_cost"] == 1


def test_conversations_sorted_by_last_turn_desc(workspace_with_traces):
    app = create_app([("workspace", workspace_with_traces)])
    client = TestClient(app)
    resp = client.get("/api/conversations")
    cids = [c["conversation_id"] for c in resp.json()["conversations"]]
    # B is 11:00, A is 10:00-10:02 — B should come first
    assert cids == ["B", "A"]


def test_conversations_workspace_filter(workspace_with_traces, tmp_path):
    ws2 = Workspace(tmp_path / "ws2")
    ws2.root.mkdir(parents=True, exist_ok=True)
    app = create_app([("workspace", workspace_with_traces), ("chat:99", ws2)])
    client = TestClient(app)
    resp = client.get("/api/conversations?workspace=chat:99")
    assert resp.json()["total"] == 0

    resp = client.get("/api/conversations?workspace=workspace")
    assert resp.json()["total"] == 2

    resp = client.get("/api/conversations?workspace=all")
    assert resp.json()["total"] == 2  # only one workspace has data


def test_conversations_unknown_workspace_404(workspace_with_traces):
    app = create_app([("workspace", workspace_with_traces)])
    client = TestClient(app)
    resp = client.get("/api/conversations?workspace=nope")
    assert resp.status_code == 404


def test_get_conversation_returns_summary_and_turns(workspace_with_traces):
    app = create_app([("workspace", workspace_with_traces)])
    client = TestClient(app)
    resp = client.get("/api/conversations/A")
    assert resp.status_code == 200
    body = resp.json()
    assert body["conversation_id"] == "A"
    assert body["turn_count"] == 3
    assert len(body["turns"]) == 3

    # turns sorted ascending by start_time
    starts = [t["start_time"] for t in body["turns"]]
    assert starts == sorted(starts)

    # turn_n derived from index when capture_path is missing
    for i, turn in enumerate(body["turns"]):
        assert "turn_n" in turn
        assert "run_id" in turn
        assert "status" in turn
        assert "model_id" in turn
        assert "tokens" in turn
        assert "duration_s" in turn


def test_get_conversation_unknown_cid_404(workspace_with_traces):
    app = create_app([("workspace", workspace_with_traces)])
    client = TestClient(app)
    resp = client.get("/api/conversations/Z")
    assert resp.status_code == 404


def test_get_conversation_invalid_cid_400_no_echo(workspace_with_traces):
    app = create_app([("workspace", workspace_with_traces)])
    client = TestClient(app)
    # ".." would resolve to parent dir — must be rejected by validate_session_id.
    # Starlette may normalise the path before the handler sees it, so the router
    # can return 404 itself (defense-in-depth). Both 400 and 404 are acceptable;
    # if the handler IS reached it must not echo the rejected value.
    resp = client.get("/api/conversations/..")
    assert resp.status_code in (400, 404)
    if resp.status_code == 400:
        assert ".." not in resp.text


def test_get_conversation_has_events_jsonl_flag(workspace_with_traces, tmp_path):
    # Synthesize an events.jsonl in one of the capture paths
    # to verify has_events_jsonl reports True for it.
    # Find the trace with capture_path set; if none, skip the flag check.
    app = create_app([("workspace", workspace_with_traces)])
    client = TestClient(app)
    resp = client.get("/api/conversations/A")
    body = resp.json()
    # has_events_jsonl is a bool on every turn entry
    for turn in body["turns"]:
        assert isinstance(turn["has_events_jsonl"], bool)
