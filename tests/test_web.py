"""Tests for blueclaw.web — trace visualization API and static serving."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from blueclaw.models import RunTrace, TraceStep
from blueclaw.web import compute_stats

# --- Fixtures ---


def _make_trace(
    run_id: str = "20260320-143022",
    goal: str = "test goal",
    model_id: str = "claude-haiku-4.5",
    status: str = "success",
    cost: float | None = 0.01,
    steps: list[TraceStep] | None = None,
) -> RunTrace:
    ts = datetime(2026, 3, 20, 14, 30, 22, tzinfo=timezone.utc)
    te = datetime(2026, 3, 20, 14, 30, 30, tzinfo=timezone.utc)
    if steps is None:
        steps = [
            TraceStep(
                index=1,
                tool_name="web_search",
                status="success",
                start_time=ts,
                end_time=te,
                duration_ms=842,
                input_summary={"query": "test"},
                output_summary="Found results...",
            )
        ]
    return RunTrace(
        run_id=run_id,
        goal=goal,
        start_time=ts,
        end_time=te,
        model_id=model_id,
        steps=steps,
        total_tokens=1840,
        total_cost=cost,
        status=status,
    )


@pytest.fixture
def web_client(tmp_path):
    from starlette.testclient import TestClient

    from blueclaw.web import create_app
    from blueclaw.workspace import Workspace

    ws_path = tmp_path / "workspace"
    ws_path.mkdir()
    (ws_path / ".blueclaw").mkdir()
    (ws_path / "CONTEXT.md").write_text("# Context\n")
    (ws_path / ".blueclaw" / "history.jsonl").write_text("")

    ws = Workspace(ws_path)
    app = create_app(ws)
    return TestClient(app), ws


# --- Index / Static ---


class TestIndex:
    def test_index_returns_html(self, web_client):
        client, _ = web_client
        r = client.get("/")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]

    def test_version_injected(self, web_client):
        from blueclaw import __version__

        client, _ = web_client
        r = client.get("/")
        assert __version__ in r.text

    def test_crab_png_served(self, web_client):
        client, _ = web_client
        r = client.get("/blueclaw-crab.png")
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/png"


# --- Trace List API ---


class TestListTraces:
    def test_list_traces_json(self, web_client):
        client, ws = web_client
        ws.write_trace(_make_trace("20260320-143022"))
        ws.write_trace(_make_trace("20260320-143100"))
        r = client.get("/api/traces")
        assert r.status_code == 200
        data = r.json()
        assert len(data["traces"]) == 2

    def test_list_traces_empty(self, web_client):
        client, _ = web_client
        r = client.get("/api/traces")
        data = r.json()
        assert data["traces"] == []
        assert data["total"] == 0

    def test_list_traces_total_count(self, web_client):
        client, ws = web_client
        ws.write_trace(_make_trace("20260320-143022"))
        ws.write_trace(_make_trace("20260320-143100"))
        ws.write_trace(_make_trace("20260320-143200"))
        r = client.get("/api/traces?limit=1")
        data = r.json()
        assert len(data["traces"]) == 1
        assert data["total"] == 3

    def test_list_traces_model_filter(self, web_client):
        client, ws = web_client
        ws.write_trace(_make_trace("20260320-143022", model_id="haiku"))
        ws.write_trace(_make_trace("20260320-143100", model_id="sonnet"))
        r = client.get("/api/traces?model=haiku")
        data = r.json()
        assert len(data["traces"]) == 1
        assert data["traces"][0]["model_id"] == "haiku"

    def test_list_traces_invalid_limit(self, web_client):
        client, _ = web_client
        r = client.get("/api/traces?limit=abc")
        assert r.status_code == 200


# --- Get Trace API ---


class TestGetTrace:
    def test_get_trace_found(self, web_client):
        client, ws = web_client
        ws.write_trace(_make_trace("20260320-143022"))
        r = client.get("/api/traces/20260320-143022")
        assert r.status_code == 200
        data = r.json()
        assert "steps" in data
        assert data["run_id"] == "20260320-143022"

    def test_get_trace_not_found(self, web_client):
        client, _ = web_client
        r = client.get("/api/traces/99999999-999999")
        assert r.status_code == 404

    def test_get_trace_path_traversal(self, web_client):
        client, _ = web_client
        r = client.get("/api/traces/../../etc/passwd")
        assert r.status_code in (400, 404)

    def test_get_trace_invalid_format(self, web_client):
        client, _ = web_client
        r = client.get("/api/traces/not-a-valid-id")
        assert r.status_code == 400


# --- Stats API ---


class TestStatsAPI:
    def test_stats_endpoint(self, web_client):
        client, ws = web_client
        ws.write_trace(_make_trace("20260320-143022"))
        ws.write_trace(_make_trace("20260320-143100"))
        r = client.get("/api/stats")
        assert r.status_code == 200
        data = r.json()
        assert data["total_runs"] == 2

    def test_stats_empty(self, web_client):
        client, _ = web_client
        r = client.get("/api/stats")
        data = r.json()
        assert data["total_runs"] == 0


# --- compute_stats unit tests ---


class TestComputeStats:
    def test_empty_traces(self):
        stats = compute_stats([])
        assert stats["total_runs"] == 0
        assert stats["total_cost"] is None
        assert stats["top_tools"] == []
        assert stats["daily_costs"] == []

    def test_basic_stats(self):
        traces = [
            _make_trace("20260320-143022", cost=0.01),
            _make_trace("20260320-143100", cost=0.02),
            _make_trace("20260320-143200", cost=0.03),
        ]
        stats = compute_stats(traces)
        assert stats["total_runs"] == 3
        assert stats["total_steps"] == 3
        assert stats["total_cost"] == 0.06
        assert len(stats["top_tools"]) == 1
        assert stats["top_tools"][0]["name"] == "web_search"

    def test_error_classification(self):
        error_step = TraceStep(
            index=1,
            tool_name="http_request",
            status="error",
            start_time=datetime(2026, 3, 20, 14, 30, 22, tzinfo=timezone.utc),
            end_time=datetime(2026, 3, 20, 14, 30, 25, tzinfo=timezone.utc),
            duration_ms=3000,
            error="ConnectionTimeout: server did not respond",
        )
        trace = _make_trace("20260320-143022", status="error", steps=[error_step])
        stats = compute_stats([trace])
        assert stats["failed_steps"] == 1
        assert stats["error_rate"] > 0
        assert any(e["category"] == "timeout" for e in stats["errors"])

    def test_daily_costs(self):
        traces = [
            _make_trace("20260320-143022", cost=0.01),
            _make_trace("20260320-143100", cost=0.02),
        ]
        stats = compute_stats(traces)
        assert len(stats["daily_costs"]) == 1
        assert stats["daily_costs"][0]["date"] == "2026-03-20"
        assert stats["daily_costs"][0]["cost"] == 0.03


# --- CLI test ---


class TestCLI:
    def test_trace_ui_help(self):
        import re as re_mod

        from typer.testing import CliRunner

        from blueclaw.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["trace", "ui", "--help"])
        assert result.exit_code == 0
        plain = re_mod.sub(r"\x1b\[[0-9;]*m", "", result.output)
        assert "--port" in plain
        assert "--no-open" in plain
