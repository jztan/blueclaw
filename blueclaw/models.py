"""Pydantic models and cost calculation for blueclaw."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, field_validator


class SessionConfig(BaseModel):
    """Configuration for a blueclaw session."""

    provider: str = "anthropic"
    model_id: str = "claude-sonnet-4-6"
    max_tokens: int = 4096
    workspace_path: Path = Path.home() / "blueclaw" / "workspace"
    allowlist_domains: list[str] = []
    tools: list[str] = ["web", "shell", "pdf"]
    trace_retention_days: int = 30
    context_strategy: str = "mask"
    context_mask_after: int = 10
    context_summarize_after: int | None = None

    @field_validator("trace_retention_days")
    @classmethod
    def clamp_retention(cls, v: int) -> int:
        return max(v, 0)

    @field_validator("context_strategy")
    @classmethod
    def validate_context_strategy(cls, v: str) -> str:
        if v not in ("mask", "summarize", "hybrid"):
            raise ValueError(f"context_strategy must be mask|summarize|hybrid, got {v}")
        return v


class RunRecord(BaseModel):
    """A single run record for history.jsonl."""

    ts: datetime
    goal: str
    tools: list[str]
    files: list[str] = []
    tokens: int
    cost: float | None = None

    def to_jsonl(self) -> str:
        """Serialize to a single-line JSON string."""
        return self.model_dump_json()

    @classmethod
    def from_jsonl(cls, line: str) -> RunRecord:
        """Deserialize from a JSON string. Raises ValueError on corrupt JSON."""
        try:
            data = json.loads(line)
        except json.JSONDecodeError as e:
            raise ValueError(f"Malformed JSONL: {e}") from e
        return cls.model_validate(data)


# Pricing: {model_id: (input_per_1k_tokens, output_per_1k_tokens)}
MODEL_PRICING: dict[str, tuple[float, float]] = {
    "claude-sonnet-4-6": (0.003, 0.015),
    "claude-sonnet-4-20250514": (0.003, 0.015),
    "claude-opus-4-6": (0.015, 0.075),
    "claude-opus-4-1-20250620": (0.015, 0.075),
    "claude-haiku-4-5-20251001": (0.0008, 0.004),
}


class TraceStep(BaseModel):
    """A single tool execution step within a trace."""

    index: int
    tool_name: str
    status: str  # "success" | "error"
    start_time: datetime
    end_time: datetime
    duration_ms: int
    input_summary: dict = {}
    output_summary: str | None = None
    error: str | None = None
    tokens: int | None = None  # v1.2
    cost: float | None = None  # v1.2


class RunTrace(BaseModel):
    """Complete trace of a single run."""

    run_id: str
    goal: str
    start_time: datetime
    end_time: datetime
    model_id: str
    steps: list[TraceStep]
    total_tokens: int
    total_cost: float | None = None
    status: str  # "success" | "error"
    context_masked_chars: int | None = None
    context_strategy: str | None = None

    def to_json(self) -> str:
        return self.model_dump_json(indent=2)

    @classmethod
    def from_json(cls, text: str) -> RunTrace:
        return cls.model_validate_json(text)


def calculate_cost(
    model_id: str, input_tokens: int, output_tokens: int
) -> float | None:
    """Calculate cost from token counts. Returns None if model not in pricing table."""
    pricing = MODEL_PRICING.get(model_id)
    if pricing is None:
        return None
    input_rate, output_rate = pricing
    return (input_tokens * input_rate + output_tokens * output_rate) / 1000


FAILURE_PATTERNS: list[tuple[str, list[str]]] = [
    ("timeout", ["timeout", "timed out", "deadline exceeded"]),
    ("rate_limit", ["rate limit", "429", "too many requests", "throttl"]),
    ("auth", ["401", "403", "unauthorized", "forbidden", "credential"]),
    ("not_found", ["404", "not found", "no such", "does not exist"]),
    ("schema", ["validation", "invalid", "schema", "type error"]),
    ("network", ["connection", "dns", "unreachable", "refused"]),
    ("sandbox", ["denied", "blocked", "not allowed", "workspace"]),
]


def classify_error(error: str | None) -> str:
    """Classify an error message into a failure category."""
    if not error:
        return "unknown"
    lower = error.lower()
    for category, patterns in FAILURE_PATTERNS:
        if any(p in lower for p in patterns):
            return category
    return "unknown"


class TestCase(BaseModel):
    """A single test case from a spec YAML."""

    goal: str
    expected_tools: list[str] = []
    expected_output_contains: str | None = None
    max_steps: int | None = None
    max_cost: float | None = None
    forbidden_tools: list[str] = []
    expected_files: list[str] = []
    expected_file_contains: dict[str, str] = {}
    forbidden_output_contains: str | None = None
    output_regex: str | None = None
    tool_order: list[str] = []
    max_duration_s: float | None = None
    runs: int = 1
    threshold: float = 0.85


class TestResult(BaseModel):
    """Result of running a single test case."""

    goal: str
    passed: bool
    verdict: str = "pass"
    failures: list[str] = []
    tools_called: list[str] = []
    steps: int = 0
    cost: float | None = None
    duration_s: float = 0.0
    error: str | None = None
    pass_count: int | None = None
    total_runs: int | None = None
    ci_lower: float | None = None
    ci_upper: float | None = None


class TestSpec(BaseModel):
    """Complete test specification loaded from YAML."""

    tests: list[TestCase]
    model: str | None = None
    allowlist_domains: list[str] = []
