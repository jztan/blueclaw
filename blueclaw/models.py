"""Pydantic models and cost calculation for blueclaw."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel


class SessionConfig(BaseModel):
    """Configuration for a blueclaw session."""

    provider: str = "anthropic"
    model_id: str = "claude-sonnet-4-6"
    max_tokens: int = 4096
    workspace_path: Path = Path.home() / "blueclaw" / "workspace"
    allowlist_domains: list[str] = []
    tools: list[str] = ["web", "github", "pdf"]


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


def calculate_cost(
    model_id: str, input_tokens: int, output_tokens: int
) -> float | None:
    """Calculate cost from token counts. Returns None if model not in pricing table."""
    pricing = MODEL_PRICING.get(model_id)
    if pricing is None:
        return None
    input_rate, output_rate = pricing
    return (input_tokens * input_rate + output_tokens * output_rate) / 1000
