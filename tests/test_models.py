"""Tests for blueclaw.models — SessionConfig, RunRecord, cost calculation."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from blueclaw.models import (
    MODEL_PRICING,
    RunRecord,
    RunTrace,
    SessionConfig,
    TraceStep,
    calculate_cost,
)

# --- SessionConfig ---


class TestSessionConfigDefaults:
    def test_session_config_defaults(self):
        cfg = SessionConfig()
        assert cfg.provider == "anthropic"
        assert cfg.model_id == "claude-sonnet-4-6"
        assert cfg.max_tokens == 4096
        assert cfg.workspace_path == Path.home() / "blueclaw" / "workspace"
        assert cfg.allowlist_domains == []
        assert cfg.tools == ["web", "github", "pdf"]

    def test_session_config_ollama(self):
        cfg = SessionConfig(provider="ollama", model_id="llama3")
        assert cfg.provider == "ollama"
        assert cfg.model_id == "llama3"

    def test_session_config_litellm(self):
        cfg = SessionConfig(provider="litellm", model_id="gemini/gemini-2.0-flash")
        assert cfg.provider == "litellm"
        assert cfg.model_id == "gemini/gemini-2.0-flash"

    def test_session_config_with_tools(self):
        cfg = SessionConfig(tools=["web", "github", "pdf"])
        assert cfg.tools == ["web", "github", "pdf"]

    def test_session_config_with_mcp(self):
        cfg = SessionConfig(tools=["web", "mcp:http://localhost:8080/sse"])
        assert "mcp:http://localhost:8080/sse" in cfg.tools

    def test_session_config_with_domain_allowlist(self):
        cfg = SessionConfig(allowlist_domains=["example.com", "docs.python.org"])
        assert cfg.allowlist_domains == ["example.com", "docs.python.org"]


# --- RunRecord ---


class TestRunRecord:
    def test_run_record_creation(self):
        ts = datetime(2026, 3, 14, 12, 0, 0, tzinfo=timezone.utc)
        rec = RunRecord(
            ts=ts,
            goal="test goal",
            tools=["web_search"],
            files=["file.md"],
            tokens=150,
            cost=0.0023,
        )
        assert rec.ts == ts
        assert rec.goal == "test goal"
        assert rec.tools == ["web_search"]
        assert rec.files == ["file.md"]
        assert rec.tokens == 150
        assert rec.cost == 0.0023

    def test_run_record_to_jsonl(self):
        ts = datetime(2026, 3, 14, 12, 0, 0, tzinfo=timezone.utc)
        rec = RunRecord(ts=ts, goal="test", tools=["t1"], tokens=100)
        line = rec.to_jsonl()
        assert isinstance(line, str)
        assert "\n" not in line
        data = json.loads(line)
        assert data["goal"] == "test"
        assert data["tokens"] == 100

    def test_run_record_from_jsonl(self):
        ts = datetime(2026, 3, 14, 12, 0, 0, tzinfo=timezone.utc)
        original = RunRecord(ts=ts, goal="test", tools=["t1"], tokens=100, cost=0.01)
        line = original.to_jsonl()
        restored = RunRecord.from_jsonl(line)
        assert restored.goal == "test"
        assert restored.tokens == 100
        assert restored.cost == 0.01

    def test_run_record_roundtrip(self):
        ts = datetime(2026, 3, 14, 12, 0, 0, tzinfo=timezone.utc)
        original = RunRecord(
            ts=ts,
            goal="search docs",
            tools=["web_search", "http_request"],
            files=["notes.md"],
            tokens=250,
            cost=0.005,
        )
        restored = RunRecord.from_jsonl(original.to_jsonl())
        assert restored == original

    def test_run_record_ts_is_iso8601(self):
        ts = datetime(2026, 3, 14, 12, 0, 0, tzinfo=timezone.utc)
        rec = RunRecord(ts=ts, goal="test", tools=[], tokens=0)
        line = rec.to_jsonl()
        data = json.loads(line)
        # Should be parseable as ISO 8601
        parsed = datetime.fromisoformat(data["ts"])
        assert parsed == ts

    def test_run_record_optional_fields(self):
        ts = datetime(2026, 3, 14, 12, 0, 0, tzinfo=timezone.utc)
        rec = RunRecord(ts=ts, goal="test", tools=[], tokens=0)
        assert rec.cost is None
        assert rec.files == []

    def test_run_record_from_jsonl_malformed(self):
        with pytest.raises(ValueError):
            RunRecord.from_jsonl("not valid json {{{")

    def test_run_record_from_jsonl_missing_fields(self):
        with pytest.raises((ValueError, ValidationError)):
            RunRecord.from_jsonl('{"goal": "test"}')


# --- Cost calculation ---


class TestCostCalculation:
    def test_calculate_cost_known_model(self):
        cost = calculate_cost("claude-sonnet-4-6", 1000, 500)
        assert cost is not None
        assert isinstance(cost, float)
        assert cost > 0

    def test_calculate_cost_unknown_model(self):
        cost = calculate_cost("ollama/llama3", 1000, 500)
        assert cost is None

    def test_calculate_cost_zero_tokens(self):
        cost = calculate_cost("claude-sonnet-4-6", 0, 0)
        assert cost == 0.0

    def test_model_pricing_is_dict(self):
        assert isinstance(MODEL_PRICING, dict)
        assert len(MODEL_PRICING) > 0


# --- TraceStep ---


class TestTraceStep:
    def test_trace_step_creation(self):
        ts = datetime(2026, 3, 15, 10, 12, 1, tzinfo=timezone.utc)
        step = TraceStep(
            index=1,
            tool_name="web_search",
            status="success",
            start_time=ts,
            end_time=ts,
            duration_ms=842,
            input_summary={"query": "MCP protocol"},
            output_summary="Found 5 results...",
        )
        assert step.tool_name == "web_search"
        assert step.duration_ms == 842
        assert step.error is None

    def test_trace_step_error(self):
        ts = datetime(2026, 3, 15, 10, 12, 1, tzinfo=timezone.utc)
        step = TraceStep(
            index=1,
            tool_name="web_fetch",
            status="error",
            start_time=ts,
            end_time=ts,
            duration_ms=100,
            error="Connection refused",
        )
        assert step.status == "error"
        assert step.error == "Connection refused"
        assert step.output_summary is None

    def test_trace_step_defaults(self):
        ts = datetime(2026, 3, 15, 10, 12, 1, tzinfo=timezone.utc)
        step = TraceStep(
            index=1,
            tool_name="t",
            status="success",
            start_time=ts,
            end_time=ts,
            duration_ms=0,
        )
        assert step.input_summary == {}
        assert step.output_summary is None
        assert step.error is None


# --- RunTrace ---


class TestRunTrace:
    def _make_trace(self):
        ts = datetime(2026, 3, 15, 10, 12, 1, tzinfo=timezone.utc)
        step = TraceStep(
            index=1,
            tool_name="web_search",
            status="success",
            start_time=ts,
            end_time=ts,
            duration_ms=842,
            input_summary={"query": "test"},
            output_summary="results",
        )
        return RunTrace(
            run_id="20260315-101201",
            goal="research MCP",
            start_time=ts,
            end_time=ts,
            model_id="claude-sonnet-4-6",
            steps=[step],
            total_tokens=2847,
            total_cost=0.0042,
            status="success",
        )

    def test_run_trace_creation(self):
        trace = self._make_trace()
        assert trace.run_id == "20260315-101201"
        assert trace.goal == "research MCP"
        assert len(trace.steps) == 1
        assert trace.total_tokens == 2847

    def test_run_trace_to_json(self):
        trace = self._make_trace()
        text = trace.to_json()
        data = json.loads(text)
        assert data["run_id"] == "20260315-101201"
        assert data["steps"][0]["tool_name"] == "web_search"

    def test_run_trace_from_json(self):
        trace = self._make_trace()
        text = trace.to_json()
        restored = RunTrace.from_json(text)
        assert restored.run_id == trace.run_id
        assert restored.goal == trace.goal
        assert len(restored.steps) == 1
        assert restored.steps[0].tool_name == "web_search"

    def test_run_trace_roundtrip(self):
        trace = self._make_trace()
        restored = RunTrace.from_json(trace.to_json())
        assert restored == trace

    def test_run_trace_no_steps(self):
        ts = datetime(2026, 3, 15, 10, 12, 1, tzinfo=timezone.utc)
        trace = RunTrace(
            run_id="20260315-101201",
            goal="simple question",
            start_time=ts,
            end_time=ts,
            model_id="claude-sonnet-4-6",
            steps=[],
            total_tokens=100,
            status="success",
        )
        assert trace.total_cost is None
        restored = RunTrace.from_json(trace.to_json())
        assert restored.steps == []

    def test_run_trace_multiple_steps(self):
        ts = datetime(2026, 3, 15, 10, 12, 1, tzinfo=timezone.utc)
        steps = [
            TraceStep(
                index=i,
                tool_name=f"tool_{i}",
                status="success",
                start_time=ts,
                end_time=ts,
                duration_ms=i * 100,
            )
            for i in range(5)
        ]
        trace = RunTrace(
            run_id="20260315-101201",
            goal="multi-step",
            start_time=ts,
            end_time=ts,
            model_id="claude-sonnet-4-6",
            steps=steps,
            total_tokens=5000,
            status="success",
        )
        restored = RunTrace.from_json(trace.to_json())
        assert len(restored.steps) == 5
        assert restored.steps[3].tool_name == "tool_3"
