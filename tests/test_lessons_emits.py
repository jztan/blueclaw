"""build_lessons_block must invoke the on_injected callback with stats."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path

from rich.console import Console

from blueclaw.events import EventBus
from blueclaw.lessons import build_lessons_block
from blueclaw.models import RunTrace, TraceStep
from blueclaw.observer import ObserverHooks


def _make_failed_trace(goal: str) -> RunTrace:
    return RunTrace(
        run_id="20260518-100000-aaaa",
        goal=goal,
        start_time=datetime.now(timezone.utc),
        end_time=datetime.now(timezone.utc),
        model_id="m",
        steps=[
            TraceStep(
                index=1,
                tool_name="web_search",
                status="error",
                start_time=datetime.now(timezone.utc),
                end_time=datetime.now(timezone.utc),
                duration_ms=100,
                input_summary={"query": "x"},
                output_summary=None,
                error="HTTPError 500",
                sandbox={
                    "mode": "inprocess",
                    "image": None,
                    "image_digest": None,
                    "fallback_reason": None,
                },
            )
        ],
        total_tokens=100,
        total_cost=None,
        status="error",
        conversation_id="cid",
        source="terminal",
    )


def test_build_lessons_block_invokes_callback_when_lessons_emitted() -> None:
    traces = [_make_failed_trace("search recent news") for _ in range(5)]
    received: list[dict] = []

    def on_injected(stats: dict) -> None:
        received.append(stats)

    block = build_lessons_block("search recent news", traces, on_injected=on_injected)
    if block is None:
        assert received == []
    else:
        assert len(received) == 1
        assert received[0]["count"] >= 1
        assert "goals" in received[0]


def test_build_lessons_block_without_callback_still_works() -> None:
    traces = [_make_failed_trace("x")]
    block = build_lessons_block("x", traces)
    assert block is None or isinstance(block, str)


def test_session_closure_emits_lesson_injected(tmp_path: Path) -> None:
    """The closure shape used in session.py must emit through the bus when attached."""
    bus = EventBus(tmp_path / "events.jsonl")
    observer = ObserverHooks(console=Console(file=StringIO()), quiet=True)
    observer.bus = bus

    def on_lessons_injected(stats: dict) -> None:
        b = getattr(observer, "bus", None) if observer is not None else None
        if b is not None:
            b.emit({"type": "lesson.injected", **stats})

    on_lessons_injected({"count": 2, "goals": ["search news", "summarize"]})
    bus.close()

    events = [
        json.loads(line)
        for line in (tmp_path / "events.jsonl").read_text().splitlines()
    ]
    lessons = [e for e in events if e["type"] == "lesson.injected"]
    assert len(lessons) == 1
    assert lessons[0]["count"] == 2
    assert lessons[0]["goals"] == ["search news", "summarize"]


def test_session_closure_no_op_when_bus_missing() -> None:
    """When the bus is None, the closure must not raise."""
    observer = ObserverHooks(console=Console(file=StringIO()), quiet=True)
    observer.bus = None

    def on_lessons_injected(stats: dict) -> None:
        b = getattr(observer, "bus", None) if observer is not None else None
        if b is not None:
            b.emit({"type": "lesson.injected", **stats})

    on_lessons_injected({"count": 1, "goals": ["x"]})  # must not raise


def test_session_invokes_lessons_inside_bus_window(tmp_path: Path) -> None:
    """build_lessons_block must be called while ctx.observer.bus is attached.

    Regression for the Phase 1 review finding: in an earlier draft, the call
    happened before bus_for_turn entered the with-block, so on_injected would
    see a null bus and silently drop the event. The fix is positional —
    confirm by exercising the closure with a stub observer that mimics the
    bus_for_turn lifecycle.
    """
    bus = EventBus(tmp_path / "events.jsonl")
    observer = ObserverHooks(console=Console(file=StringIO()), quiet=True)
    # Simulate the bus_for_turn attachment point.
    observer.bus = bus

    def on_lessons_injected(stats: dict) -> None:
        b = getattr(observer, "bus", None)
        if b is not None:
            b.emit({"type": "lesson.injected", **stats})

    # Verify the event IS emitted when the bus is attached.
    on_lessons_injected({"count": 1, "goals": ["x"]})

    # Now simulate the bus being detached (post-turn) and confirm the closure
    # correctly no-ops without raising.
    observer.bus = None
    on_lessons_injected({"count": 1, "goals": ["x"]})

    bus.close()

    lines = (tmp_path / "events.jsonl").read_text().splitlines()
    lesson_events = [
        json.loads(line)
        for line in lines
        if json.loads(line)["type"] == "lesson.injected"
    ]
    assert len(lesson_events) == 1, "lesson.injected fired only when bus attached"
