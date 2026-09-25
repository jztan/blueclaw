"""End-to-end recovery of a large tool result after context masking."""

from __future__ import annotations

import json
from io import StringIO

import pytest
from rich.console import Console
from strands import Agent, tool
from strands.models.model import Model

from blueclaw.context import ObservationMaskingManager
from blueclaw.observer import ObserverHooks
from blueclaw.tool_outputs import ToolOutputStore, extract_artifact_refs
from blueclaw.tools.retrieve_output import make_retrieve_output
from blueclaw.workspace import Workspace


def _model_tool_call_events(call_id: str, name: str, arguments: dict) -> list[dict]:
    return [
        {"messageStart": {"role": "assistant"}},
        {
            "contentBlockStart": {
                "contentBlockIndex": 0,
                "start": {"toolUse": {"toolUseId": call_id, "name": name}},
            }
        },
        {
            "contentBlockDelta": {
                "contentBlockIndex": 0,
                "delta": {"toolUse": {"input": json.dumps(arguments)}},
            }
        },
        {"contentBlockStop": {"contentBlockIndex": 0}},
        {"messageStop": {"stopReason": "tool_use"}},
        {
            "metadata": {
                "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
                "metrics": {"latencyMs": 1},
            }
        },
    ]


class RecoveryModel(Model):
    def __init__(self):
        self.calls = 0
        self.masked_source_kept_reference = False
        self.retrieved_fact_seen = False
        self.retrieve_calls = 0

    def update_config(self, **model_config):
        pass

    def get_config(self):
        return {"model_id": "tool-output-recovery-test"}

    async def structured_output(
        self, output_model, prompt, system_prompt=None, **kwargs
    ):
        raise NotImplementedError
        yield

    async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
        call = self.calls
        self.calls += 1
        if call == 0:
            events = _model_tool_call_events("large-1", "large_result", {})
        elif call == 1:
            events = _model_tool_call_events("noop-1", "noop", {})
        elif call == 2:
            result_texts = [
                item.get("text", "")
                for message in messages
                for block in message.get("content", [])
                if "toolResult" in block
                for item in block["toolResult"].get("content", [])
            ]
            refs = extract_artifact_refs("\n".join(result_texts))
            assert refs
            self.masked_source_kept_reference = any(
                text.startswith("[output omitted") and refs[0] in text
                for text in result_texts
            )
            self.retrieve_calls += 1
            events = _model_tool_call_events(
                "retrieve-1",
                "retrieve_tool_output",
                {"artifact_ref": refs[0], "query": "RECOVERY-FACT-731"},
            )
        elif call == 3:
            result_texts = [
                item.get("text", "")
                for message in messages
                for block in message.get("content", [])
                if "toolResult" in block
                for item in block["toolResult"].get("content", [])
            ]
            self.retrieved_fact_seen = any(
                "RECOVERY-FACT-731" in text for text in result_texts
            )
            events = [
                {"messageStart": {"role": "assistant"}},
                {"contentBlockStart": {"contentBlockIndex": 0, "start": {}}},
                {
                    "contentBlockDelta": {
                        "contentBlockIndex": 0,
                        "delta": {"text": "Found RECOVERY-FACT-731"},
                    }
                },
                {"contentBlockStop": {"contentBlockIndex": 0}},
                {"messageStop": {"stopReason": "end_turn"}},
                {
                    "metadata": {
                        "usage": {
                            "inputTokens": 1,
                            "outputTokens": 1,
                            "totalTokens": 2,
                        },
                        "metrics": {"latencyMs": 1},
                    }
                },
            ]
        else:
            raise AssertionError(f"unexpected model invocation {call}")

        for event in events:
            yield event


@pytest.mark.asyncio
async def test_agent_retrieves_middle_fact_after_source_result_is_masked(tmp_path):
    capture = tmp_path / ".blueclaw" / "conversations" / "case-a" / "turns" / "turn-001"
    capture.mkdir(parents=True)
    large_calls = 0

    @tool
    def large_result() -> str:
        """Return the deterministic long fixture once."""
        nonlocal large_calls
        large_calls += 1
        return "x" * 8_000 + " RECOVERY-FACT-731 " + "y" * 8_000

    @tool
    def noop() -> str:
        """Return a small result to age the earlier tool output."""
        return "aged"

    workspace = Workspace(tmp_path)
    observer = ObserverHooks(
        console=Console(file=StringIO()),
        output_store=ToolOutputStore(tmp_path),
    )
    observer.capture_path = capture
    model = RecoveryModel()
    agent = Agent(
        model=model,
        tools=[large_result, noop, make_retrieve_output(workspace)],
        hooks=[observer],
        conversation_manager=ObservationMaskingManager(mask_after=1),
        callback_handler=None,
    )

    result = await agent.invoke_async("Find the recovery fact")

    assert large_calls == 1
    assert result.stop_reason == "end_turn"
    assert model.retrieve_calls == 1
    assert model.masked_source_kept_reference
    assert model.retrieved_fact_seen
