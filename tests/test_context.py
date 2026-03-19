"""Tests for blueclaw.context — observation masking conversation manager."""

from unittest.mock import MagicMock, Mock, patch

from blueclaw.context import ObservationMaskingManager

# --- Helpers ---


def _make_tool_use(tool_id="t1", name="shell_command", input_data=None):
    return {
        "role": "assistant",
        "content": [
            {
                "toolUse": {
                    "toolUseId": tool_id,
                    "name": name,
                    "input": input_data or {},
                }
            }
        ],
    }


def _make_tool_result(tool_id="t1", text="output text", status="success"):
    return {
        "role": "user",
        "content": [
            {
                "toolResult": {
                    "toolUseId": tool_id,
                    "content": [{"text": text}],
                    "status": status,
                }
            }
        ],
    }


def _make_user_text(text="hello"):
    return {"role": "user", "content": [{"text": text}]}


def _make_assistant_text(text="response"):
    return {"role": "assistant", "content": [{"text": text}]}


def _build_conversation(n):
    """Build n tool-use/tool-result pairs with bookend text messages."""
    msgs = [_make_user_text("start")]
    for i in range(n):
        tid = f"t{i}"
        msgs.append(_make_tool_use(tool_id=tid, name=f"tool_{i}"))
        msgs.append(_make_tool_result(tool_id=tid, text=f"result_{i} " * 50))
    msgs.append(_make_assistant_text("done"))
    return msgs


def _count_masked(messages):
    count = 0
    for msg in messages:
        for c in msg.get("content", []):
            if "toolResult" in c:
                text = c["toolResult"]["content"][0]["text"]
                if text.startswith("[output omitted"):
                    count += 1
    return count


# --- Core masking ---


class TestObservationMasking:
    def test_no_masking_under_threshold(self):
        mgr = ObservationMaskingManager(mask_after=10)
        agent = MagicMock()
        agent.messages = _build_conversation(5)
        mgr.apply_management(agent)
        assert _count_masked(agent.messages) == 0

    def test_masks_old_turns(self):
        mgr = ObservationMaskingManager(mask_after=10)
        agent = MagicMock()
        agent.messages = _build_conversation(15)
        mgr.apply_management(agent)
        assert _count_masked(agent.messages) == 5

    def test_preserves_tool_use_blocks(self):
        mgr = ObservationMaskingManager(mask_after=5)
        agent = MagicMock()
        agent.messages = _build_conversation(10)
        original = sum(
            1
            for m in agent.messages
            if m.get("role") == "assistant"
            and any("toolUse" in c for c in m.get("content", []))
        )
        mgr.apply_management(agent)
        after = sum(
            1
            for m in agent.messages
            if m.get("role") == "assistant"
            and any("toolUse" in c for c in m.get("content", []))
        )
        assert original == after

    def test_preserves_message_count(self):
        mgr = ObservationMaskingManager(mask_after=5)
        agent = MagicMock()
        agent.messages = _build_conversation(10)
        original_count = len(agent.messages)
        mgr.apply_management(agent)
        assert len(agent.messages) == original_count

    def test_idempotent(self):
        mgr = ObservationMaskingManager(mask_after=5)
        agent = MagicMock()
        agent.messages = _build_conversation(10)
        mgr.apply_management(agent)
        snapshot = [str(m) for m in agent.messages]
        mgr.apply_management(agent)
        assert [str(m) for m in agent.messages] == snapshot

    def test_masked_chars_accumulated(self):
        mgr = ObservationMaskingManager(mask_after=5)
        agent = MagicMock()
        agent.messages = _build_conversation(10)
        mgr.apply_management(agent)
        assert mgr.masked_chars > 0

    def test_reset_metrics(self):
        mgr = ObservationMaskingManager(mask_after=5)
        agent = MagicMock()
        agent.messages = _build_conversation(10)
        mgr.apply_management(agent)
        mgr.reset_metrics()
        assert mgr.masked_chars == 0

    def test_skips_empty_tool_results(self):
        mgr = ObservationMaskingManager(mask_after=1)
        agent = MagicMock()
        agent.messages = [
            _make_user_text("start"),
            _make_tool_use(tool_id="t0"),
            _make_tool_result(tool_id="t0", text=""),
            _make_tool_use(tool_id="t1"),
            _make_tool_result(tool_id="t1", text="recent"),
        ]
        mgr.apply_management(agent)
        # Empty result should not be masked
        tr = agent.messages[2]["content"][0]["toolResult"]
        assert tr["content"][0]["text"] == ""

    def test_multi_tool_assistant_counts_as_one_turn(self):
        mgr = ObservationMaskingManager(mask_after=2)
        agent = MagicMock()
        # One assistant msg with 2 toolUse blocks = 1 turn
        multi_tool_msg = {
            "role": "assistant",
            "content": [
                {"toolUse": {"toolUseId": "a", "name": "t1", "input": {}}},
                {"toolUse": {"toolUseId": "b", "name": "t2", "input": {}}},
            ],
        }
        agent.messages = [
            _make_user_text("start"),
            multi_tool_msg,
            _make_tool_result(tool_id="a", text="out1"),
            _make_tool_result(tool_id="b", text="out2"),
            _make_tool_use(tool_id="c"),
            _make_tool_result(tool_id="c", text="out3"),
        ]
        mgr.apply_management(agent)
        # 2 tool turns, keep 2 → nothing masked
        assert _count_masked(agent.messages) == 0

    def test_cutoff_boundary_preserves_recent_tool_result(self):
        """The toolResult for the Mth-from-last turn must not be masked."""
        mgr = ObservationMaskingManager(mask_after=2)
        agent = MagicMock()
        # _build_conversation(4) produces:
        #   0: user_text, 1: toolUse0, 2: toolResult0,
        #   3: toolUse1, 4: toolResult1,
        #   5: toolUse2, 6: toolResult2,  ← cutoff is at index 5 (2nd from end)
        #   7: toolUse3, 8: toolResult3,
        #   9: assistant_text
        agent.messages = _build_conversation(4)
        mgr.apply_management(agent)
        assert _count_masked(agent.messages) == 2
        # toolResult2 (index 6) is for the cutoff turn — must NOT be masked
        tr = agent.messages[6]["content"][0]["toolResult"]
        assert not tr["content"][0]["text"].startswith("[output omitted")


# --- reduce_context ---


class TestReduceContext:
    def test_aggressive_mask_all(self):
        mgr = ObservationMaskingManager(mask_after=10)
        agent = MagicMock()
        agent.messages = _build_conversation(5)
        with patch.object(mgr._summarizer, "reduce_context"):
            mgr.reduce_context(agent)
        # mask_after=0 masks before the last toolUse, so last result stays
        # With 5 turns, cutoff at last toolUse → 4 masked
        assert _count_masked(agent.messages) == 4

    def test_delegates_to_summarizer(self):
        mgr = ObservationMaskingManager(mask_after=10)
        agent = MagicMock()
        agent.messages = _build_conversation(3)
        with patch.object(mgr._summarizer, "reduce_context") as mock_reduce:
            mgr.reduce_context(agent)
        mock_reduce.assert_called_once()


# --- Hybrid mode ---


class TestHybridMode:
    def test_summarize_triggered_above_threshold(self):
        mgr = ObservationMaskingManager(mask_after=5, summarize_after=10)
        agent = MagicMock()
        agent.messages = _build_conversation(15)
        with patch.object(mgr._summarizer, "reduce_context") as mock_reduce:
            mgr.apply_management(agent)
        mock_reduce.assert_called_once()

    def test_no_summarize_below_threshold(self):
        mgr = ObservationMaskingManager(mask_after=5, summarize_after=43)
        agent = MagicMock()
        agent.messages = _build_conversation(10)
        with patch.object(mgr._summarizer, "reduce_context") as mock_reduce:
            mgr.apply_management(agent)
        mock_reduce.assert_not_called()


# --- BeforeModelCallEvent hook ---


class TestBeforeModelCallHook:
    def test_masking_runs_on_before_model_call(self):
        mgr = ObservationMaskingManager(mask_after=2)
        event = Mock()
        event.agent = MagicMock()
        event.agent.messages = _build_conversation(5)
        mgr._on_before_model_call(event)
        assert _count_masked(event.agent.messages) == 3

    def test_register_hooks_adds_callback(self):
        from strands.hooks import BeforeModelCallEvent

        mgr = ObservationMaskingManager()
        registry = Mock()
        mgr.register_hooks(registry)
        registry.add_callback.assert_called_once_with(
            BeforeModelCallEvent, mgr._on_before_model_call
        )
