# tests/test_events_jsonl_schema.py
"""Every event in events.jsonl must carry {seq, ts, type} at minimum.

This is a regression guard: future event-type additions must not skip
the base shape. The dashboard depends on these three fields for sort,
display, and dedup.
"""

from __future__ import annotations

import json
from pathlib import Path

from blueclaw.events import EventBus


def test_every_event_has_minimum_shape(tmp_path: Path) -> None:
    bus = EventBus(tmp_path / "events.jsonl")
    bus.emit({"type": "tool.before", "tool_name": "x"})
    bus.emit({"type": "tool.after", "tool_name": "x", "status": "success"})
    bus.emit({"type": "model.before", "model_id": "m"})
    bus.emit({"type": "model.after", "duration_ms": 12})
    bus.emit({"type": "message.added", "role": "assistant", "text_chars": 100})
    bus.emit({"type": "context.mask", "masked_chars": 42, "replaced_steps": 1})
    bus.emit({"type": "lesson.injected", "count": 2, "goals": ["g"]})
    bus.close()

    lines = (tmp_path / "events.jsonl").read_text().splitlines()
    seqs = []
    for line in lines:
        ev = json.loads(line)
        assert "seq" in ev, f"missing seq: {ev}"
        assert "ts" in ev, f"missing ts: {ev}"
        assert "type" in ev, f"missing type: {ev}"
        assert isinstance(ev["seq"], int)
        assert isinstance(ev["ts"], str)
        assert isinstance(ev["type"], str)
        seqs.append(ev["seq"])
    # seq monotonic from 0
    assert seqs == list(range(len(seqs)))
