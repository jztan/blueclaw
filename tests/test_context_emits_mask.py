"""ObservationMaskingManager must emit a context.mask event when it masks output."""

from __future__ import annotations

import json
from pathlib import Path

from blueclaw.context import ObservationMaskingManager
from blueclaw.events import EventBus


def test_mask_emits_event(tmp_path: Path) -> None:
    bus = EventBus(tmp_path / "events.jsonl")
    mgr = ObservationMaskingManager(mask_after=2)
    mgr.bus = bus

    fake_agent = type("Agent", (), {})()
    fake_agent.messages = [
        {"role": "user", "content": [{"text": "hi"}]},
        {
            "role": "user",
            "content": [
                {
                    "toolResult": {
                        "toolUseId": "u1",
                        "content": [{"text": "A" * 5000}],
                        "status": "success",
                    }
                }
            ],
        },
        {
            "role": "assistant",
            "content": [{"toolUse": {"name": "t", "toolUseId": "u1", "input": {}}}],
        },
        {
            "role": "user",
            "content": [
                {
                    "toolResult": {
                        "toolUseId": "u2",
                        "content": [{"text": "B" * 5000}],
                        "status": "success",
                    }
                }
            ],
        },
        {
            "role": "assistant",
            "content": [{"toolUse": {"name": "t", "toolUseId": "u2", "input": {}}}],
        },
        {"role": "user", "content": [{"text": "now"}]},
        {
            "role": "assistant",
            "content": [{"toolUse": {"name": "t", "toolUseId": "u3", "input": {}}}],
        },
        {
            "role": "assistant",
            "content": [{"toolUse": {"name": "t", "toolUseId": "u4", "input": {}}}],
        },
    ]
    mgr._apply_masking(fake_agent)
    bus.close()

    events = [
        json.loads(line)
        for line in (tmp_path / "events.jsonl").read_text().splitlines()
    ]
    masks = [e for e in events if e["type"] == "context.mask"]
    assert len(masks) >= 1, f"no context.mask event; saw {[e['type'] for e in events]}"
    m = masks[0]
    assert m["masked_chars"] > 0
    assert m["replaced_steps"] >= 1
    assert m["strategy"] == "mask"


def test_mask_without_bus_is_silent() -> None:
    """No bus attached = no emit = no crash."""
    mgr = ObservationMaskingManager(mask_after=2)
    assert mgr.bus is None
    fake_agent = type("Agent", (), {})()
    fake_agent.messages = []
    mgr._apply_masking(fake_agent)  # must not raise
