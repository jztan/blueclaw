"""Pydantic models and cost calculation for blueclaw."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class ExtraMount(BaseModel):
    """A user-declared bind mount for the docker sandbox."""

    host: str
    container: str
    mode: Literal["ro", "rw"] = "ro"


_SYSTEM_DENYLIST = frozenset(
    {"/", "/etc", "/var", "/usr", "/bin", "/sbin", "/boot", "/root"}
)


def _validate_extra_mount(m: ExtraMount) -> ExtraMount:
    expanded = Path(os.path.expanduser(m.host))
    host_path = expanded.resolve()
    # Check both expanded (pre-symlink-resolution) and resolved paths against
    # the deny-list, since macOS resolves /etc -> /private/etc, /var -> /private/var.
    if str(expanded) in _SYSTEM_DENYLIST or str(host_path) in _SYSTEM_DENYLIST:
        raise ValueError(f"extra_mounts: host path {expanded} is on the deny-list")
    if host_path == Path(os.path.expanduser("~/.ssh")):
        raise ValueError("extra_mounts: host path ~/.ssh is on the deny-list")
    home = Path(os.path.expanduser("~"))
    if host_path == home:
        raise ValueError(
            f"extra_mounts: host path {host_path} (HOME) is on the deny-list"
        )
    workspace = home / "blueclaw" / "workspace"
    if workspace.is_relative_to(host_path):
        raise ValueError(
            f"extra_mounts: host path {host_path} is an ancestor of the workspace mount"
        )
    return m


class SandboxConfig(BaseModel):
    """Sandbox isolation configuration. See docs/sandbox.md."""

    mode: Literal["inprocess", "docker"] = "inprocess"
    image: str | None = None
    network: Literal["bridge", "none", "proxy"] = "bridge"
    cpu: float = 1.0
    memory_mb: int = 1024
    pids: int = 512
    on_unavailable: Literal["error", "fallback"] = "error"
    user: str = "host"
    env_files: list[Path] | None = None
    extra_mounts: list[ExtraMount] = []
    extra_env: dict[str, str] = {}

    @field_validator("network")
    @classmethod
    def reject_proxy(cls, v: str) -> str:
        if v == "proxy":
            raise ValueError(
                "network: proxy is reserved for v3 and not yet implemented"
            )
        return v

    @field_validator("cpu")
    @classmethod
    def cpu_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError(f"cpu must be > 0, got {v}")
        return v

    @field_validator("memory_mb")
    @classmethod
    def memory_minimum(cls, v: int) -> int:
        if v < 64:
            raise ValueError(f"memory_mb must be >= 64, got {v}")
        return v

    @field_validator("pids")
    @classmethod
    def pids_minimum(cls, v: int) -> int:
        if v < 16:
            raise ValueError(f"pids must be >= 16, got {v}")
        return v

    @field_validator("user")
    @classmethod
    def validate_user(cls, v: str) -> str:
        if v == "host":
            return v
        parts = v.split(":")
        if len(parts) != 2:
            raise ValueError(f"user must be 'host' or 'uid:gid', got {v!r}")
        try:
            uid, gid = int(parts[0]), int(parts[1])
        except ValueError as e:
            raise ValueError(f"user must be 'host' or 'uid:gid', got {v!r}") from e
        if uid < 0 or gid < 0:
            raise ValueError(f"user uid/gid must be non-negative, got {v!r}")
        return v

    @field_validator("extra_mounts")
    @classmethod
    def validate_extra_mounts(cls, v: list[ExtraMount]) -> list[ExtraMount]:
        return [_validate_extra_mount(m) for m in v]


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
    sandbox: SandboxConfig = SandboxConfig()
    bridges: dict = Field(default_factory=dict)

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


class TelegramBridgeConfig(BaseModel):
    """Config block for `blueclaw telegram`. Loaded from `bridges.telegram`."""

    bot_token: str
    allowed_chat_ids: list[int] = Field(default_factory=list)
    allowed_user_ids: list[int] = Field(default_factory=list)
    mode: str = "polling"
    webhook_url: str | None = None
    webhook_port: int = 8421
    chats_root: Path = Path.home() / "blueclaw" / "chats"

    @field_validator("mode")
    @classmethod
    def _validate_mode(cls, v: str) -> str:
        if v not in ("polling", "webhook"):
            raise ValueError("mode must be 'polling' or 'webhook'")
        return v

    @field_validator("chats_root", mode="before")
    @classmethod
    def _expand_chats_root(cls, v):
        if isinstance(v, str):
            return Path(v).expanduser()
        return v


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
    sandbox: dict[str, str | None] | None = None


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
    source: str = "terminal"  # "terminal" | "api" | "eval" | "telegram"
    conversation_id: str | None = None
    capture_path: str | None = None
    # Relative to workspace.root, e.g. ".blueclaw/turns/<cid>/turn-005".
    # `None` is INTENTIONALLY AMBIGUOUS — covers both:
    #   - no capture written by design (e.g. HTTP request with no cid)
    #   - trace predates the capture feature (pre-2026-05-18)
    # Do NOT add a third semantic to this value; if you need to distinguish
    # the two cases, introduce a separate field.

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
    forbidden_output_regex: str | None = None
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
    artifacts_path: str | None = None


class TestSpec(BaseModel):
    """Complete test specification loaded from YAML."""

    tests: list[TestCase]
    model: str | None = None
    allowlist_domains: list[str] = []
