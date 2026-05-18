"""ObserverHooks must emit tool events through its attached EventBus."""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

from rich.console import Console

from blueclaw.events import EventBus
from blueclaw.observer import ObserverHooks


def _fake_tool_use(tool_id: str, name: str, input_: dict) -> dict:
    return {"toolUseId": tool_id, "name": name, "input": input_}


def test_observer_emits_tool_before_and_after(tmp_path: Path) -> None:
    bus = EventBus(tmp_path / "events.jsonl")
    obs = ObserverHooks(console=Console(file=StringIO()), quiet=True)
    obs.bus = bus

    before_ev = SimpleNamespace(
        tool_use=_fake_tool_use("u1", "web_search", {"query": "x"}),
        cancel_tool=None,
        invocation_state={},
    )
    obs.before_tool(before_ev)

    after_ev = SimpleNamespace(
        tool_use=_fake_tool_use("u1", "web_search", {"query": "x"}),
        result={"content": [{"text": "result"}]},
        exception=None,
    )
    obs.after_tool(after_ev)

    bus.close()

    events = [
        json.loads(line)
        for line in (tmp_path / "events.jsonl").read_text().splitlines()
    ]
    types = [e["type"] for e in events]
    assert "tool.before" in types
    assert "tool.after" in types
    before = next(e for e in events if e["type"] == "tool.before")
    assert before["tool_name"] == "web_search"
    assert before["tool_use_id"] == "u1"
    assert before["input"] == {"query": "x"}
    after = next(e for e in events if e["type"] == "tool.after")
    assert after["tool_use_id"] == "u1"
    assert after["status"] == "success"
    assert "duration_ms" in after
    assert "output_chars" in after


def test_observer_without_bus_is_silent(tmp_path: Path) -> None:
    """Bus is optional — pre-Phase-1 callers without a bus must not break."""
    obs = ObserverHooks(console=Console(file=StringIO()), quiet=True)
    assert obs.bus is None

    before_ev = SimpleNamespace(
        tool_use=_fake_tool_use("u1", "x", {}),
        cancel_tool=None,
        invocation_state={},
    )
    # Must not raise
    obs.before_tool(before_ev)
