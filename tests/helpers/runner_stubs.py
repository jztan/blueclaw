"""Shared runner stubs for adapter capture tests.

Stubs `blueclaw.runner.runner_session` and `blueclaw.runner.run_turn` so
adapter integration tests can exercise the capture-path wiring without
making real model calls. The real `finalize`, `finalize_error`, and
`next_capture_path` are left untouched — those are the code paths under
test.
"""

from __future__ import annotations

import contextlib
import io
from datetime import datetime, timezone

from rich.console import Console


class _StubAgent:
    def __init__(self):
        self.messages = [{"role": "assistant", "content": [{"text": "ok"}]}]
        self.state = type("S", (), {"set": lambda self, *a, **kw: None})()

    def _result(self):
        return type(
            "R",
            (),
            {
                "message": {"role": "assistant", "content": [{"text": "ok"}]},
                "metrics": type("M", (), {"accumulated_usage": {}})(),
                "stop_reason": "end_turn",
            },
        )()

    def __call__(self, _input):
        return self._result()

    async def stream_async(self, _input):
        yield {"data": "ok"}
        yield {"result": self._result()}


def install_stub_runner(monkeypatch):
    """Patch runner_session / run_turn across all import sites."""
    from blueclaw.observer import ObserverHooks
    from blueclaw.runner import RunnerCtx, finalize

    @contextlib.contextmanager
    def _stub_runner_session(*args, **kwargs):
        agent = _StubAgent()
        observer = ObserverHooks(console=Console(file=io.StringIO()), quiet=True)
        observer.mcp_clients = []
        observer.conversation_manager = None
        yield RunnerCtx(observer=observer, agent=agent)

    def _stub_run_turn(*args, **kwargs):
        with _stub_runner_session() as ctx:
            start = datetime.now(timezone.utc)
            agent_input = args[3] if len(args) > 3 else kwargs.get("agent_input")
            result = ctx.agent(agent_input)
            end = datetime.now(timezone.utc)
            return finalize(
                ctx,
                result,
                goal=kwargs.get("goal", "stub"),
                source=kwargs.get("source", "stub"),
                conversation_id=kwargs.get("conversation_id"),
                start_time=start,
                end_time=end,
                config=args[0] if args else kwargs["config"],
                capture_path=kwargs.get("capture_path"),
                workspace_root=kwargs.get("workspace_root"),
            )

    monkeypatch.setattr("blueclaw.runner.runner_session", _stub_runner_session)
    monkeypatch.setattr("blueclaw.runner.run_turn", _stub_run_turn)
    monkeypatch.setattr("blueclaw.bridges.core.run_turn", _stub_run_turn, raising=False)
    monkeypatch.setattr(
        "blueclaw.server.runner_session", _stub_runner_session, raising=False
    )
