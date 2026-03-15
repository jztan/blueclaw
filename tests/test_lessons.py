"""Tests for blueclaw.lessons — trace-powered behavioral hints."""

from datetime import datetime, timezone

from blueclaw.lessons import (
    MAX_LESSONS,
    SIMILARITY_THRESHOLD,
    _extract_hints,
    _goal_words,
    _is_problematic,
    build_lessons_block,
    goal_similarity,
)
from blueclaw.models import RunTrace, TraceStep

TS = datetime(2026, 3, 15, 12, 0, 0, tzinfo=timezone.utc)


def _step(tool="web_search", status="success", error=None, input_summary=None):
    return TraceStep(
        index=1,
        tool_name=tool,
        status=status,
        start_time=TS,
        end_time=TS,
        duration_ms=100,
        input_summary=input_summary or {},
        error=error,
    )


def _trace(goal="test goal", steps=None, status="success", cost=None):
    return RunTrace(
        run_id="20260315-120000",
        goal=goal,
        start_time=TS,
        end_time=TS,
        model_id="claude-sonnet-4-6",
        steps=steps or [],
        total_tokens=100,
        total_cost=cost,
        status=status,
    )


# --- Goal similarity ---


class TestGoalSimilarity:
    def test_identical_goals(self):
        assert goal_similarity("oil price today", "oil price today") == 1.0

    def test_similar_goals(self):
        sim = goal_similarity("oil futures price today", "crude oil price today")
        assert sim >= SIMILARITY_THRESHOLD

    def test_unrelated_goals(self):
        sim = goal_similarity("oil futures price", "write python unittest")
        assert sim < SIMILARITY_THRESHOLD

    def test_empty_goal(self):
        assert goal_similarity("", "something") == 0.0

    def test_stopwords_ignored(self):
        words = _goal_words("the price of oil in the market")
        assert "the" not in words
        assert "of" not in words
        assert "oil" in words


# --- Problematic trace detection ---


class TestIsProblematic:
    def test_error_status(self):
        assert _is_problematic(_trace(status="error")) is True

    def test_cost_spike(self):
        assert _is_problematic(_trace(cost=0.15)) is True

    def test_many_steps(self):
        steps = [_step() for _ in range(12)]
        assert _is_problematic(_trace(steps=steps)) is True

    def test_multiple_errors(self):
        steps = [_step(status="error", error="fail") for _ in range(3)]
        assert _is_problematic(_trace(steps=steps)) is True

    def test_clean_trace(self):
        steps = [_step(), _step()]
        assert _is_problematic(_trace(steps=steps, cost=0.01)) is False


# --- Hint extraction ---


class TestExtractHints:
    def test_repeated_tool_failure(self):
        steps = [
            _step(tool="http_request", status="error", error="HTTP 429"),
            _step(tool="http_request", status="error", error="HTTP 429"),
        ]
        hints = _extract_hints(_trace(steps=steps))
        assert any("http_request" in h and "2x" in h for h in hints)

    def test_domain_failure(self):
        steps = [
            _step(
                tool="http_request",
                status="error",
                error="HTTP 429 Too Many Requests",
                input_summary={"url": "https://finance.yahoo.com/quote"},
            ),
        ]
        hints = _extract_hints(_trace(steps=steps))
        assert any("finance.yahoo.com" in h for h in hints)

    def test_step_spike_hint(self):
        steps = [_step() for _ in range(12)]
        hints = _extract_hints(_trace(steps=steps))
        assert any("12 steps" in h for h in hints)

    def test_cost_spike_hint(self):
        hints = _extract_hints(_trace(cost=0.25, steps=[_step()]))
        assert any("$0.25" in h for h in hints)

    def test_no_hints_for_clean_trace(self):
        hints = _extract_hints(_trace(steps=[_step()], cost=0.01))
        assert hints == []


# --- Lessons block ---


class TestBuildLessonsBlock:
    def test_no_traces(self):
        assert build_lessons_block("oil price", []) is None

    def test_no_similar_traces(self):
        traces = [_trace(goal="write python test", status="error")]
        assert build_lessons_block("oil price today", traces) is None

    def test_no_problematic_traces(self):
        traces = [_trace(goal="oil price today", steps=[_step()], cost=0.01)]
        assert build_lessons_block("oil price today", traces) is None

    def test_matching_problematic_trace(self):
        steps = [
            _step(
                tool="http_request",
                status="error",
                error="HTTP 429",
                input_summary={"url": "https://yahoo.com/api"},
            ),
            _step(
                tool="http_request",
                status="error",
                error="HTTP 429",
                input_summary={"url": "https://yahoo.com/api"},
            ),
        ]
        traces = [_trace(goal="oil price today", steps=steps, status="error")]
        block = build_lessons_block("oil futures price today", traces)
        assert block is not None
        assert "Trace Lessons" in block

    def test_max_lessons_cap(self):
        steps = [
            _step(tool=f"tool_{i}", status="error", error=f"Error {i}")
            for i in range(10)
        ]
        # Make each tool fail twice so each generates a hint
        steps = steps + steps
        traces = [_trace(goal="oil price", steps=steps, status="error", cost=0.50)]
        block = build_lessons_block("oil price", traces)
        assert block is not None
        # Count bullet points
        lesson_lines = [line for line in block.split("\n") if line.startswith("- ")]
        assert len(lesson_lines) <= MAX_LESSONS

    def test_lessons_block_format(self):
        steps = [_step() for _ in range(12)]
        traces = [_trace(goal="check oil price", steps=steps)]
        block = build_lessons_block("check oil price", traces)
        assert block is not None
        assert block.startswith("## Trace Lessons")
        assert "\n- " in block
