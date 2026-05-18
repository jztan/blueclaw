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
