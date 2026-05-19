"""Trace lessons — extract behavioral hints from past execution traces."""

from __future__ import annotations

from typing import Callable

from blueclaw.models import RunTrace, classify_error

MAX_LESSONS = 3
MAX_TRACES = 50
SIMILARITY_THRESHOLD = 0.3
COST_SPIKE_THRESHOLD = 0.10  # dollars
STEP_SPIKE_THRESHOLD = 10


def _stem(word: str) -> str:
    """Minimal suffix stripping for keyword matching."""
    for suffix in ("ing", "tion", "s", "ed", "ly"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word


_STOPWORDS = {
    "a",
    "an",
    "the",
    "is",
    "to",
    "for",
    "of",
    "in",
    "on",
    "and",
    "how",
    "what",
    "do",
    "does",
    "can",
    "my",
    "me",
    "it",
    "this",
    "that",
    "be",
    "are",
    "was",
    "were",
    "about",
}


def _goal_words(goal: str) -> set[str]:
    """Extract stemmed lowercase keyword set from a goal string."""
    words = set()
    for raw in goal.lower().split():
        w = raw.strip("?.,!:;\"'()[]")
        if w and w not in _STOPWORDS and len(w) > 1:
            words.add(_stem(w))
    return words


def goal_similarity(a: str, b: str) -> float:
    """Jaccard similarity between goal keyword sets."""
    wa, wb = _goal_words(a), _goal_words(b)
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def _is_problematic(trace: RunTrace) -> bool:
    """True if the trace had failures, cost spikes, or excessive steps."""
    if trace.status == "error":
        return True
    if (trace.total_cost or 0) >= COST_SPIKE_THRESHOLD:
        return True
    if len(trace.steps) >= STEP_SPIKE_THRESHOLD:
        return True
    error_count = sum(1 for s in trace.steps if s.status == "error")
    if error_count >= 3:
        return True
    return False


def _extract_hints(trace: RunTrace) -> list[str]:
    """Extract short hint strings from a problematic trace."""
    hints: list[str] = []

    # Group errors by category
    error_cats: dict[str, list[str]] = {}
    for step in trace.steps:
        if step.error:
            cat = classify_error(step.error)
            detail = step.error[:80]
            error_cats.setdefault(cat, []).append(f"{step.tool_name}: {detail}")

    # Repeated tool failures
    tool_errors: dict[str, int] = {}
    for step in trace.steps:
        if step.status == "error":
            tool_errors[step.tool_name] = tool_errors.get(step.tool_name, 0) + 1
    for tool, count in tool_errors.items():
        if count >= 2:
            # Find the dominant error category for this tool
            cat_for_tool = "unknown"
            for step in trace.steps:
                if step.tool_name == tool and step.error:
                    cat_for_tool = classify_error(step.error)
                    break
            hints.append(f"{tool} failed {count}x ({cat_for_tool})")

    # Domain-specific failures (extract domains from http_request inputs)
    for step in trace.steps:
        if step.tool_name == "http_request" and step.status == "error":
            url = step.input_summary.get("url", "")
            if "://" in url:
                domain = url.split("://", 1)[-1].split("/")[0]
                cat = classify_error(step.error)
                hint = f"{domain} returns {cat}"
                if hint not in hints:
                    hints.append(hint)

    # Cost/step spike
    if len(trace.steps) >= STEP_SPIKE_THRESHOLD:
        hints.append(
            f"similar goal used {len(trace.steps)} steps — prefer fewer tool calls"
        )
    if (trace.total_cost or 0) >= COST_SPIKE_THRESHOLD:
        hints.append(f"similar goal cost ${trace.total_cost:.2f} — be concise")

    return hints


def build_lessons_block(
    goal: str,
    traces: list[RunTrace],
    *,
    on_injected: Callable[[dict], None] | None = None,
) -> str | None:
    """Build a lessons block for the system prompt. Returns None if no lessons.

    If on_injected is provided, called once with {"count": N, "goals": [...]}
    when a non-empty block is returned. Used to emit a lesson.injected event
    from the adapter without making this module bus-aware.
    """
    # Filter to recent problematic traces with similar goals
    candidates: list[tuple[float, RunTrace]] = []
    for trace in traces[:MAX_TRACES]:
        if not _is_problematic(trace):
            continue
        sim = goal_similarity(goal, trace.goal)
        if sim >= SIMILARITY_THRESHOLD:
            candidates.append((sim, trace))

    if not candidates:
        return None

    # Best matches first
    candidates.sort(key=lambda x: x[0], reverse=True)

    # Collect hints, deduplicate, cap at MAX_LESSONS
    seen: set[str] = set()
    lessons: list[str] = []
    for _, trace in candidates:
        for hint in _extract_hints(trace):
            if hint not in seen:
                seen.add(hint)
                lessons.append(hint)
            if len(lessons) >= MAX_LESSONS:
                break
        if len(lessons) >= MAX_LESSONS:
            break

    if not lessons:
        return None

    if on_injected is not None:
        on_injected(
            {
                "count": len(lessons),
                "goals": [t.goal for t in traces if t.goal][:5],
            }
        )

    lines = ["## Trace Lessons\n"]
    for lesson in lessons:
        lines.append(f"- {lesson}")
    return "\n".join(lines)
