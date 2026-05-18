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


# ---------------------------------------------------------------------------
# Task 7 — model.before / model.after / message.added
# ---------------------------------------------------------------------------


def _fake_agent(
    model_id: str = "claude-test",
    messages: list | None = None,
    system_prompt: str = "You are helpful.",
    tool_names: list | None = None,
) -> SimpleNamespace:
    """Minimal agent-like object matching what BeforeModelCallEvent.agent exposes."""
    model = SimpleNamespace(config={"model_id": model_id})
    return SimpleNamespace(
        model=model,
        messages=messages if messages is not None else [],
        system_prompt=system_prompt,
        tool_names=tool_names if tool_names is not None else ["web_search", "shell"],
    )


def _fake_stop_response(
    stop_reason: str = "end_turn",
    input_tokens: int = 100,
    output_tokens: int = 50,
    cache_read: int = 10,
    cache_creation: int = 5,
    latency_ms: int = 1234,
) -> SimpleNamespace:
    """Minimal ModelStopResponse-like object."""
    message = {
        "role": "assistant",
        "content": [{"text": "hello"}],
        "metadata": {
            "usage": {
                "inputTokens": input_tokens,
                "outputTokens": output_tokens,
                "totalTokens": input_tokens + output_tokens,
                "cacheReadInputTokens": cache_read,
                "cacheWriteInputTokens": cache_creation,
            },
            "metrics": {"latencyMs": latency_ms},
        },
    }
    return SimpleNamespace(stop_reason=stop_reason, message=message)


def test_observer_emits_model_before_and_after(tmp_path: Path) -> None:
    """before_model and after_model emit correctly-shaped events."""
    bus = EventBus(tmp_path / "events.jsonl")
    obs = ObserverHooks(console=Console(file=StringIO()), quiet=True)
    obs.bus = bus

    agent = _fake_agent(
        model_id="claude-test",
        messages=[{"role": "user", "content": [{"text": "hi"}]}],
        system_prompt="Be helpful.",
        tool_names=["web_search", "shell"],
    )

    # Fire model.before
    before_ev = SimpleNamespace(
        agent=agent, invocation_state={}, projected_input_tokens=None
    )
    obs.before_model(before_ev)

    # Fire model.after with a successful stop_response
    stop_resp = _fake_stop_response(
        stop_reason="end_turn",
        input_tokens=100,
        output_tokens=50,
        cache_read=10,
        cache_creation=5,
        latency_ms=1234,
    )
    after_ev = SimpleNamespace(
        agent=agent,
        invocation_state={},
        stop_response=stop_resp,
        exception=None,
    )
    obs.after_model(after_ev)

    bus.close()

    events = [
        json.loads(line)
        for line in (tmp_path / "events.jsonl").read_text().splitlines()
    ]
    types = [e["type"] for e in events]
    assert "model.before" in types
    assert "model.after" in types

    mb = next(e for e in events if e["type"] == "model.before")
    assert mb["model_id"] == "claude-test"
    assert mb["prompt_messages"] == 1
    assert mb["system_prompt_chars"] == len("Be helpful.")
    assert mb["tools_provided"] == ["web_search", "shell"]

    ma = next(e for e in events if e["type"] == "model.after")
    assert ma["duration_ms"] == 1234
    assert ma["input_tokens"] == 100
    assert ma["output_tokens"] == 50
    assert ma["cache_read"] == 10
    assert ma["cache_creation"] == 5
    assert ma["stop_reason"] == "end_turn"


def test_observer_emits_message_added(tmp_path: Path) -> None:
    """on_message_added emits role, text_chars, and tool_uses counts."""
    bus = EventBus(tmp_path / "events.jsonl")
    obs = ObserverHooks(console=Console(file=StringIO()), quiet=True)
    obs.bus = bus

    agent = _fake_agent()

    # Message with one text block and two toolUse blocks
    message = {
        "role": "assistant",
        "content": [
            {"text": "I will search for that."},
            {"toolUse": {"toolUseId": "t1", "name": "web_search", "input": {}}},
            {"toolUse": {"toolUseId": "t2", "name": "shell", "input": {}}},
        ],
    }
    ev = SimpleNamespace(agent=agent, message=message)
    obs.on_message_added(ev)

    bus.close()

    events = [
        json.loads(line)
        for line in (tmp_path / "events.jsonl").read_text().splitlines()
    ]
    message_events = [e for e in events if e["type"] == "message.added"]
    assert len(message_events) == 1
    e = message_events[0]
    assert e["role"] == "assistant"
    assert e["text_chars"] == len("I will search for that.")
    assert e["tool_uses"] == 2


def test_observer_model_hooks_no_op_without_bus(tmp_path: Path) -> None:
    """All three model/message callbacks are silent when bus is None."""
    obs = ObserverHooks(console=Console(file=StringIO()), quiet=True)
    assert obs.bus is None

    agent = _fake_agent()

    before_ev = SimpleNamespace(
        agent=agent, invocation_state={}, projected_input_tokens=None
    )
    after_ev = SimpleNamespace(
        agent=agent,
        invocation_state={},
        stop_response=_fake_stop_response(),
        exception=None,
    )
    message_ev = SimpleNamespace(
        agent=agent,
        message={"role": "user", "content": [{"text": "hello"}]},
    )

    # None of these must raise
    obs.before_model(before_ev)
    obs.after_model(after_ev)
    obs.on_message_added(message_ev)
