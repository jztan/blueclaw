"""End-to-end HTTP Stop with a real Strands agent and owned shell process."""

import asyncio
import os
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from strands.models.model import Model

from blueclaw.models import SessionConfig
from blueclaw.server import create_server_app
from blueclaw.workspace import Workspace


class ShellModel(Model):
    def __init__(self):
        self.calls = 0

    def update_config(self, **model_config):
        pass

    def get_config(self):
        return {"model_id": "deterministic-shell"}

    async def structured_output(
        self, output_model, prompt, system_prompt=None, **kwargs
    ):
        raise NotImplementedError
        yield

    async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
        self.calls += 1
        yield {"messageStart": {"role": "assistant"}}
        if self.calls == 1:
            yield {"contentBlockStart": {"contentBlockIndex": 0, "start": {}}}
            yield {
                "contentBlockDelta": {
                    "contentBlockIndex": 0,
                    "delta": {"text": "prefix"},
                }
            }
            yield {"contentBlockStop": {"contentBlockIndex": 0}}
            yield {
                "contentBlockStart": {
                    "contentBlockIndex": 1,
                    "start": {
                        "toolUse": {"toolUseId": "shell-1", "name": "shell_command"}
                    },
                }
            }
            yield {
                "contentBlockDelta": {
                    "contentBlockIndex": 1,
                    "delta": {
                        "toolUse": {
                            "input": (
                                '{"command":"printf ready > marker; '
                                'echo $$ > shell.pid; exec sleep 20"}'
                            )
                        }
                    },
                }
            }
            yield {"contentBlockStop": {"contentBlockIndex": 1}}
            reason = "tool_use"
        else:
            yield {"contentBlockStart": {"contentBlockIndex": 0, "start": {}}}
            yield {
                "contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "done"}}
            }
            yield {"contentBlockStop": {"contentBlockIndex": 0}}
            reason = "end_turn"
        yield {"messageStop": {"stopReason": reason}}
        yield {
            "metadata": {
                "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
                "metrics": {"latencyMs": 1},
            }
        }


@pytest.mark.skipif(os.name != "posix", reason="process groups require POSIX")
@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/message", "/message/stream"])
async def test_http_stop_reaps_real_sdk_shell_and_preserves_partial(
    tmp_path: Path, path
):
    workspace = Workspace(tmp_path / "ws")
    config = SessionConfig(workspace_path=workspace.root, tools=["shell"])
    model = ShellModel()
    with patch("blueclaw.server.BackgroundContextUpdater", return_value=None):
        app = create_server_app(config, workspace, model=model)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            pending = asyncio.create_task(
                client.post(
                    path,
                    json={
                        "message": "run shell",
                        "request_id": "shell-stop",
                        "conversation_id": "c",
                    },
                )
            )
            try:
                for _ in range(200):
                    if (workspace.root / "marker").exists() and (
                        workspace.root / "shell.pid"
                    ).exists():
                        break
                    await asyncio.sleep(0.01)
                assert (workspace.root / "marker").exists()
                assert (workspace.root / "shell.pid").exists()
                pid = int((workspace.root / "shell.pid").read_text())
                assert (
                    await client.post("/requests/shell-stop/cancel")
                ).status_code == 202
                response = await asyncio.wait_for(pending, 5)
                next_turn = await client.post(
                    "/message",
                    json={
                        "message": "again",
                        "request_id": "next",
                        "conversation_id": "c",
                    },
                )
                assert next_turn.status_code == 200
                assert next_turn.json()["status"] == "success"
            finally:
                if not pending.done():
                    pending.cancel()
                    await asyncio.gather(pending, return_exceptions=True)
    if path.endswith("stream"):
        assert "event: stopped" in response.text
    else:
        assert response.json()["status"] == "cancelled"
    captures = [p.read_text() for p in workspace.root.rglob("response.txt")]
    assert "prefix" in captures
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    assert sorted(trace.status for trace in workspace.list_traces()) == [
        "cancelled",
        "success",
    ]
