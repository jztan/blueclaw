"""Pydantic models and cost calculation for blueclaw."""

from __future__ import annotations

import json
import re
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
    max_concurrent_runs: int = 4

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

    @field_validator("max_concurrent_runs")
    @classmethod
    def validate_max_concurrent(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"max_concurrent_runs must be >= 1, got {v}")
        return v


class RunRecord(BaseModel):
    """A single run record for history.jsonl."""

    ts: datetime
    goal: str
    tools: list[str]
    files: list[str] = []
    tokens: int
    cost: float | None = None
    conversation_id: str | None = None

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


class MessageRequest(BaseModel):
    """Incoming request for POST /message."""

    message: str
    conversation_id: str | None = None
    file_ids: list[str] = []

    @field_validator("conversation_id")
    @classmethod
    def validate_conversation_id(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not re.match(r"^[a-zA-Z0-9_-]{1,64}$", v):
            raise ValueError(
                "conversation_id must be 1-64 alphanumeric/dash/underscore chars"
            )
        return v


class MessageResponse(BaseModel):
    """Response body for POST /message."""

    reply: str
    run_id: str
    conversation_id: str | None
    tokens: int
    cost: float | None


class UploadResponse(BaseModel):
    """Response body for POST /upload."""

    file_id: str
    filename: str
    mime_type: str
    size_bytes: int
    conversation_id: str


# Date the pricing table was last reviewed against provider list prices.
# Bump this when you edit MODEL_PRICING_PER_M. Stale > ~6 months → re-check.
PRICING_UPDATED = "2026-05-10"

# Anthropic prompt-caching multipliers, applied to the base input rate.
# Cache reads get a 90% discount; 5-minute TTL cache writes pay a 25% premium.
CACHE_READ_RATE = 0.1
CACHE_WRITE_RATE = 1.25

# Pricing per 1M tokens — matches Anthropic's published pricing page 1:1.
# {model_id: (input_per_1M, output_per_1M)}
MODEL_PRICING_PER_M: dict[str, tuple[float, float]] = {
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-sonnet-4-20250514": (3.0, 15.0),
    "claude-opus-4-6": (15.0, 75.0),
    "claude-opus-4-1-20250620": (15.0, 75.0),
    "claude-haiku-4-5-20251001": (0.80, 4.0),
}

# Backward-compatible alias for callers that imported the old name.
MODEL_PRICING = MODEL_PRICING_PER_M


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
    source: str = "terminal"  # "terminal" | "api"
    conversation_id: str | None = None

    def to_json(self) -> str:
        return self.model_dump_json(indent=2)

    @classmethod
    def from_json(cls, text: str) -> RunTrace:
        return cls.model_validate_json(text)


def calculate_cost(
    model_id: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float | None:
    """Calculate cost from token counts. Returns None if model not in pricing table.

    cache_read_tokens / cache_write_tokens are Anthropic prompt-caching counts;
    pass 0 (default) for providers that don't expose them. Anthropic reports
    cached tokens *separately* from input_tokens, so adding the cache columns
    does not double-count.
    """
    pricing = MODEL_PRICING_PER_M.get(model_id)
    if pricing is None:
        return None
    input_rate, output_rate = pricing
    return (
        input_tokens * input_rate
        + output_tokens * output_rate
        + cache_read_tokens * input_rate * CACHE_READ_RATE
        + cache_write_tokens * input_rate * CACHE_WRITE_RATE
    ) / 1_000_000


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
