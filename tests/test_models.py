"""Tests for blueclaw.models — SessionConfig, RunRecord, cost calculation."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from blueclaw.models import (
    MODEL_PRICING,
    RunRecord,
    SessionConfig,
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
