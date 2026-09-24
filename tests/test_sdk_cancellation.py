"""Characterize the Strands cancellation boundary used by HTTP Stop."""

import asyncio
import json
import threading

import pytest
from strands import Agent, tool
from strands.models.model import Model


class PausingModel(Model):
    def __init__(self):
        self.paused = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0

    def update_config(self, **model_config):
        pass

    def get_config(self):
        return {"model_id": "deterministic"}

    async def structured_output(
        self, output_model, prompt, system_prompt=None, **kwargs
    ):
        raise NotImplementedError
        yield

    async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
        self.calls += 1
        yield {"messageStart": {"role": "assistant"}}
        yield {"contentBlockStart": {"contentBlockIndex": 0, "start": {}}}
        yield {
            "contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "prefix"}}
        }
        if self.calls == 1:
            self.paused.set()
            await self.release.wait()
        yield {"contentBlockStop": {"contentBlockIndex": 0}}
        yield {"messageStop": {"stopReason": "end_turn"}}
        yield {
            "metadata": {
                "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
                "metrics": {"latencyMs": 1},
            }
        }


class ToolModel(PausingModel):
    async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
        self.calls += 1
        yield {"messageStart": {"role": "assistant"}}
        if self.calls == 1:
            yield {
                "contentBlockStart": {
                    "contentBlockIndex": 0,
                    "start": {"toolUse": {"toolUseId": "call-1", "name": "wait_tool"}},
                }
            }
            yield {
                "contentBlockDelta": {
                    "contentBlockIndex": 0,
                    "delta": {"toolUse": {"input": "{}"}},
                }
            }
            yield {"contentBlockStop": {"contentBlockIndex": 0}}
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


@pytest.mark.asyncio
async def test_sdk_stop_waits_for_next_provider_chunk():
    model = PausingModel()
    agent = Agent(model=model, callback_handler=None)
    events = []

    async def collect():
        async for event in agent.stream_async("first"):
            events.append(event)

    task = asyncio.create_task(collect())
    try:
        await asyncio.wait_for(model.paused.wait(), 2)
        assert agent.cancel() is None
        await asyncio.sleep(0)
        assert not task.done()
        model.release.set()
        await asyncio.wait_for(task, 2)
        result = next(e["result"] for e in events if "result" in e)
        assert result.stop_reason == "cancelled"
        assert "prefix" in "".join(e.get("data", "") for e in events)
        assert "prefix" not in json.dumps(result.message)
        again = await agent.invoke_async("second")
        assert again.stop_reason == "end_turn"
    finally:
        model.release.set()
        await asyncio.wait_for(task, 2)


@pytest.mark.asyncio
async def test_sdk_stop_waits_for_running_sync_tool_but_loop_remains_live():
    entered = threading.Event()
    release = threading.Event()

    @tool
    def wait_tool() -> str:
        """Wait for release."""
        entered.set()
        release.wait(timeout=3)
        return "released"

    agent = Agent(model=ToolModel(), tools=[wait_tool], callback_handler=None)
    task = asyncio.create_task(agent.invoke_async("call tool"))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        heartbeat = asyncio.create_task(asyncio.sleep(0))
        await asyncio.wait_for(heartbeat, 1)
        agent.cancel()
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        release.set()
    result = await asyncio.wait_for(task, 2)
    assert result.stop_reason == "cancelled"
