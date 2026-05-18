"""End-to-end: a turn executed through run_turn writes a well-formed events.jsonl."""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock

from rich.console import Console


def test_run_turn_writes_events_jsonl(tmp_path: Path, monkeypatch) -> None:
    from blueclaw.models import SessionConfig
    from blueclaw.runner import next_capture_path, run_turn
    from blueclaw.workspace import Workspace
    from blueclaw import runner as runner_mod
    from blueclaw.observer import ObserverHooks

    ws_root = tmp_path / "ws"
    ws_root.mkdir(parents=True, exist_ok=True)
    (ws_root / ".blueclaw").mkdir(exist_ok=True)
    workspace = Workspace(ws_root)

    cid = "cid-test"
    capture_path = next_capture_path(workspace.root, cid)

    class FakeAgent:
        def __init__(self, **kwargs):
            self.observer: ObserverHooks = kwargs["observer"]
            self.messages = []
            self.system_prompt = ""

        def __call__(self, agent_input):
            if self.observer.bus is not None:
                self.observer.bus.emit(
                    {
                        "type": "message.added",
                        "role": "assistant",
                        "text_chars": 5,
                        "tool_uses": 0,
                    }
                )
            result = MagicMock()
            result.message = {"role": "assistant", "content": [{"text": "hello"}]}
            result.metrics.accumulated_usage = {
                "totalTokens": 10,
                "inputTokens": 5,
                "outputTokens": 5,
            }
            result.stop_reason = "end_turn"
            return result

    def fake_create_agent(**kwargs):
        return FakeAgent(**kwargs)

    monkeypatch.setattr(runner_mod, "create_agent", fake_create_agent)
    monkeypatch.setattr(runner_mod, "cleanup_mcp_clients", lambda *a, **k: None)
    monkeypatch.setattr(
        runner_mod,
        "build_trace_and_record",
        lambda *a, **k: (
            MagicMock(model_dump=lambda **_: {}),
            MagicMock(model_dump=lambda **_: {}),
        ),
    )

    config = SessionConfig(model_provider="anthropic", model_id="x")
    model = MagicMock()
    run_turn(
        config,
        workspace,
        model,
        agent_input="hi",
        goal="hi",
        source="terminal",
        conversation_id=cid,
        capture_path=capture_path,
        workspace_root=workspace.root,
    )

    events_path = capture_path / "events.jsonl"
    assert events_path.exists(), f"events.jsonl missing at {events_path}"
    lines = events_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) >= 2  # at least schema.version + message.added
    first = json.loads(lines[0])
    assert first["type"] == "schema.version"
    assert first["seq"] == 0
    types = [json.loads(line)["type"] for line in lines]
    assert "message.added" in types
    seqs = [json.loads(line)["seq"] for line in lines]
    assert seqs == list(range(len(seqs)))

    # Bus closed cleanly: file ends with a newline.
    assert events_path.read_bytes().endswith(b"\n"), "events.jsonl not flushed/closed"


def test_run_turn_masking_manager_bus_is_attached(tmp_path: Path) -> None:
    """bus_for_turn must attach the bus to observer.conversation_manager too,
    not just observer. Otherwise context.mask events vanish in production."""
    from blueclaw.runner import bus_for_turn
    from blueclaw.observer import ObserverHooks

    class FakeMaskingManager:
        bus = None

    observer = ObserverHooks(console=Console(file=StringIO()), quiet=True)
    observer.conversation_manager = FakeMaskingManager()

    capture_path = tmp_path / "cap"
    with bus_for_turn(observer, capture_path) as bus:
        assert bus is not None
        assert observer.bus is bus
        assert observer.conversation_manager.bus is bus

    assert observer.bus is None
    assert observer.conversation_manager.bus is None
