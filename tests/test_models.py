"""Tests for blueclaw.models — SessionConfig, RunRecord, cost calculation."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from blueclaw.models import (
    MODEL_PRICING,
    MessageRequest,
    MessageResponse,
    RunRecord,
    RunTrace,
    SessionConfig,
    TraceStep,
    calculate_cost,
    classify_error,
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
        assert cfg.tools == ["web", "shell", "pdf"]

    def test_session_config_ollama(self):
        cfg = SessionConfig(provider="ollama", model_id="llama3")
        assert cfg.provider == "ollama"
        assert cfg.model_id == "llama3"

    def test_session_config_litellm(self):
        cfg = SessionConfig(provider="litellm", model_id="gemini/gemini-2.0-flash")
        assert cfg.provider == "litellm"
        assert cfg.model_id == "gemini/gemini-2.0-flash"

    def test_session_config_with_tools(self):
        cfg = SessionConfig(tools=["web", "shell", "pdf"])
        assert cfg.tools == ["web", "shell", "pdf"]

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


# --- v1.2: TraceStep token/cost fields ---


class TestTraceStepTokenFields:
    """v1.2: optional per-step token/cost fields."""

    def test_defaults_to_none(self):
        """New fields default to None when not provided."""
        ts = datetime(2026, 3, 15, 10, 12, 1, tzinfo=timezone.utc)
        step = TraceStep(
            index=1,
            tool_name="web_search",
            status="success",
            start_time=ts,
            end_time=ts,
            duration_ms=842,
        )
        assert step.tokens is None
        assert step.cost is None

    def test_explicit_values(self):
        """Tokens and cost can be set explicitly."""
        ts = datetime(2026, 3, 15, 10, 12, 1, tzinfo=timezone.utc)
        step = TraceStep(
            index=1,
            tool_name="web_search",
            status="success",
            start_time=ts,
            end_time=ts,
            duration_ms=842,
            tokens=500,
            cost=0.0015,
        )
        assert step.tokens == 500
        assert step.cost == 0.0015

    def test_backward_compat_from_json(self):
        """Existing JSON without tokens/cost deserializes correctly."""
        ts = datetime(2026, 3, 15, 10, 12, 1, tzinfo=timezone.utc)
        step = TraceStep(
            index=1,
            tool_name="web_search",
            status="success",
            start_time=ts,
            end_time=ts,
            duration_ms=842,
        )
        trace = RunTrace(
            run_id="20260315-101201",
            goal="test",
            start_time=ts,
            end_time=ts,
            model_id="claude-sonnet-4-6",
            steps=[step],
            total_tokens=100,
            status="success",
        )
        # Serialize, strip tokens/cost keys to simulate old format
        data = json.loads(trace.to_json())
        for s in data["steps"]:
            s.pop("tokens", None)
            s.pop("cost", None)
        restored = RunTrace.from_json(json.dumps(data))
        assert restored.steps[0].tokens is None
        assert restored.steps[0].cost is None

    def test_roundtrip_with_values(self):
        """tokens/cost survive to_json() -> from_json() roundtrip."""
        ts = datetime(2026, 3, 15, 10, 12, 1, tzinfo=timezone.utc)
        step = TraceStep(
            index=1,
            tool_name="web_search",
            status="success",
            start_time=ts,
            end_time=ts,
            duration_ms=842,
            tokens=500,
            cost=0.0015,
        )
        trace = RunTrace(
            run_id="20260315-101201",
            goal="test",
            start_time=ts,
            end_time=ts,
            model_id="claude-sonnet-4-6",
            steps=[step],
            total_tokens=500,
            total_cost=0.0015,
            status="success",
        )
        restored = RunTrace.from_json(trace.to_json())
        assert restored.steps[0].tokens == 500
        assert restored.steps[0].cost == 0.0015


# --- v1.2: classify_error ---


class TestClassifyError:
    """v1.2: heuristic error classification."""

    def test_none_returns_unknown(self):
        assert classify_error(None) == "unknown"

    def test_empty_string_returns_unknown(self):
        assert classify_error("") == "unknown"

    def test_no_match_returns_unknown(self):
        assert classify_error("something went wrong") == "unknown"

    def test_timeout(self):
        assert classify_error("Request timed out after 30s") == "timeout"

    def test_timeout_deadline(self):
        assert classify_error("deadline exceeded") == "timeout"

    def test_rate_limit(self):
        assert classify_error("rate limit exceeded, retry after 60s") == "rate_limit"

    def test_rate_limit_429(self):
        assert classify_error("HTTP 429: too many requests") == "rate_limit"

    def test_auth_401(self):
        assert classify_error("HTTP 401 Unauthorized") == "auth"

    def test_auth_forbidden(self):
        assert classify_error("403 Forbidden: insufficient permissions") == "auth"

    def test_not_found(self):
        assert classify_error("404 Not Found") == "not_found"

    def test_not_found_no_such(self):
        assert classify_error("No such file or directory") == "not_found"

    def test_schema(self):
        assert classify_error("Validation error: field 'name' required") == "schema"

    def test_schema_invalid(self):
        assert classify_error("Invalid parameter type") == "schema"

    def test_network(self):
        assert classify_error("Connection refused by host") == "network"

    def test_network_dns(self):
        assert classify_error("DNS resolution failed") == "network"

    def test_sandbox(self):
        assert classify_error("Command denied by workspace policy") == "sandbox"

    def test_sandbox_blocked(self):
        assert classify_error("Path blocked: outside workspace") == "sandbox"

    def test_case_insensitive(self):
        assert classify_error("TIMEOUT EXCEEDED") == "timeout"
        assert classify_error("CONNECTION REFUSED") == "network"

    def test_first_match_wins(self):
        # "connection timeout" matches both timeout and network
        # timeout comes first in FAILURE_PATTERNS
        assert classify_error("connection timeout") == "timeout"


class TestSessionConfigContext:
    def test_default_context_strategy(self):
        cfg = SessionConfig()
        assert cfg.context_strategy == "mask"
        assert cfg.context_mask_after == 10
        assert cfg.context_summarize_after is None

    def test_valid_strategies(self):
        for s in ("mask", "summarize", "hybrid"):
            cfg = SessionConfig(context_strategy=s)
            assert cfg.context_strategy == s

    def test_invalid_strategy_raises(self):
        with pytest.raises(ValidationError):
            SessionConfig(context_strategy="invalid")

    def test_hybrid_fields(self):
        cfg = SessionConfig(
            context_strategy="hybrid",
            context_mask_after=5,
            context_summarize_after=43,
        )
        assert cfg.context_mask_after == 5
        assert cfg.context_summarize_after == 43


class TestRunTraceContextFields:
    def test_context_fields_default_none(self):
        trace = RunTrace(
            run_id="20260316-120000",
            goal="test",
            start_time=datetime(2026, 3, 16, tzinfo=timezone.utc),
            end_time=datetime(2026, 3, 16, tzinfo=timezone.utc),
            model_id="test",
            steps=[],
            total_tokens=0,
            status="success",
        )
        assert trace.context_masked_chars is None
        assert trace.context_strategy is None

    def test_context_fields_populated(self):
        trace = RunTrace(
            run_id="20260316-120000",
            goal="test",
            start_time=datetime(2026, 3, 16, tzinfo=timezone.utc),
            end_time=datetime(2026, 3, 16, tzinfo=timezone.utc),
            model_id="test",
            steps=[],
            total_tokens=0,
            status="success",
            context_masked_chars=5000,
            context_strategy="mask",
        )
        assert trace.context_masked_chars == 5000

    def test_backward_compat_old_json(self):
        old_json = (
            '{"run_id":"x","goal":"y",'
            '"start_time":"2026-03-16T00:00:00Z",'
            '"end_time":"2026-03-16T00:00:00Z",'
            '"model_id":"m","steps":[],"total_tokens":0,"status":"success"}'
        )
        trace = RunTrace.from_json(old_json)
        assert trace.context_masked_chars is None

    def test_roundtrip_with_context_fields(self):
        trace = RunTrace(
            run_id="20260316-120000",
            goal="test",
            start_time=datetime(2026, 3, 16, tzinfo=timezone.utc),
            end_time=datetime(2026, 3, 16, tzinfo=timezone.utc),
            model_id="test",
            steps=[],
            total_tokens=0,
            status="success",
            context_masked_chars=1234,
            context_strategy="hybrid",
        )
        restored = RunTrace.from_json(trace.to_json())
        assert restored.context_masked_chars == 1234
        assert restored.context_strategy == "hybrid"


# --- MessageRequest ---


class TestMessageRequest:
    def test_message_required(self):
        with pytest.raises(ValidationError):
            MessageRequest()

    def test_message_only(self):
        req = MessageRequest(message="hello")
        assert req.message == "hello"
        assert req.conversation_id is None

    def test_conversation_id_valid_chars(self):
        req = MessageRequest(message="hi", conversation_id="abc-123_XYZ")
        assert req.conversation_id == "abc-123_XYZ"

    def test_conversation_id_rejects_slash(self):
        with pytest.raises(ValidationError, match="conversation_id"):
            MessageRequest(message="hi", conversation_id="../../etc/passwd")

    def test_conversation_id_rejects_too_long(self):
        with pytest.raises(ValidationError):
            MessageRequest(message="hi", conversation_id="a" * 65)

    def test_conversation_id_rejects_empty_string(self):
        with pytest.raises(ValidationError):
            MessageRequest(message="hi", conversation_id="")


# --- MessageResponse ---


class TestMessageResponse:
    def test_fields_present(self):
        resp = MessageResponse(
            reply="answer",
            run_id="20260322-130015-a3f1",
            conversation_id="sess-001",
            tokens=150,
            cost=0.0023,
        )
        assert resp.reply == "answer"
        assert resp.cost == 0.0023
        assert resp.conversation_id == "sess-001"

    def test_cost_nullable(self):
        resp = MessageResponse(
            reply="answer",
            run_id="20260322-130015-a3f1",
            conversation_id=None,
            tokens=100,
            cost=None,
        )
        assert resp.cost is None


# --- RunTrace.source field ---


class TestRunTraceSourceField:
    def _base_trace(self, **kwargs):
        ts = datetime(2026, 3, 22, tzinfo=timezone.utc)
        return RunTrace(
            run_id="20260322-130000",
            goal="test",
            start_time=ts,
            end_time=ts,
            model_id="claude-sonnet-4-6",
            steps=[],
            total_tokens=0,
            status="success",
            **kwargs,
        )

    def test_default_source_is_terminal(self):
        trace = self._base_trace()
        assert trace.source == "terminal"

    def test_source_api(self):
        trace = self._base_trace(source="api")
        assert trace.source == "api"

    def test_existing_traces_without_source_deserialize_ok(self):
        json_str = (
            '{"run_id":"20260322-130000","goal":"test",'
            '"start_time":"2026-03-22T00:00:00Z","end_time":"2026-03-22T00:00:00Z",'
            '"model_id":"claude-sonnet-4-6","steps":[],'
            '"total_tokens":0,"status":"success"}'
        )
        trace = RunTrace.from_json(json_str)
        assert trace.source == "terminal"
