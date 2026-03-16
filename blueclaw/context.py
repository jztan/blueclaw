"""Smart context management — observation masking with optional hybrid summarization."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from strands.agent.conversation_manager import (
    ConversationManager,
    SummarizingConversationManager,
)
from strands.hooks import BeforeModelCallEvent, HookRegistry

if TYPE_CHECKING:
    from strands.agent.agent import Agent

logger = logging.getLogger(__name__)

MASK_PLACEHOLDER = "[output omitted -- {n} chars]"


class ObservationMaskingManager(ConversationManager):
    """Replace old tool results with placeholders instead of summarizing.

    Based on Lindenbauer et al. 2025 "The Complexity Trap" — observation
    masking preserves all reasoning while replacing distant environment
    observations with a size placeholder.  Halves cost with no quality loss.
    """

    def __init__(
        self,
        mask_after: int = 10,
        summarize_after: int | None = None,
        summary_ratio: float = 0.3,
    ) -> None:
        super().__init__()
        self.mask_after = mask_after
        self.summarize_after = summarize_after
        self._summarizer = SummarizingConversationManager(
            summary_ratio=summary_ratio,
        )
        self._masked_chars = 0

    @property
    def masked_chars(self) -> int:
        return self._masked_chars

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        super().register_hooks(registry, **kwargs)
        registry.add_callback(BeforeModelCallEvent, self._on_before_model_call)

    def _on_before_model_call(self, event: BeforeModelCallEvent) -> None:
        self._apply_masking(event.agent)

    def apply_management(self, agent: "Agent", **kwargs: Any) -> None:
        self._apply_masking(agent)
        if self.summarize_after is not None:
            if _count_tool_turns(agent.messages) > self.summarize_after:
                try:
                    self._summarizer.reduce_context(agent)
                except Exception:
                    logger.debug("Hybrid summarization skipped", exc_info=True)

    def reduce_context(
        self, agent: "Agent", e: Exception | None = None, **kwargs: Any
    ) -> None:
        # Aggressive: mask ALL tool results
        self._apply_masking(agent, override_mask_after=0)
        # Delegate to summarizer for further reduction
        self._summarizer.reduce_context(agent, e=e)

    # ------------------------------------------------------------------

    def _apply_masking(
        self, agent: "Agent", override_mask_after: int | None = None
    ) -> None:
        messages = agent.messages
        m = override_mask_after if override_mask_after is not None else self.mask_after
        cutoff = _find_mask_cutoff(messages, m)
        if cutoff <= 0:
            return
        for i in range(cutoff):
            self._mask_tool_results(messages[i])

    def _mask_tool_results(self, message: dict) -> None:
        if message.get("role") != "user":
            return
        for block in message.get("content", []):
            if "toolResult" not in block:
                continue
            tr = block["toolResult"]
            items = tr.get("content", [])
            total = sum(len(item.get("text", "")) for item in items)
            if total == 0:
                continue
            # Already masked — skip
            if len(items) == 1 and items[0].get("text", "").startswith(
                "[output omitted"
            ):
                continue
            self._masked_chars += total
            tr["content"] = [{"text": MASK_PLACEHOLDER.format(n=total)}]

    def reset_metrics(self) -> None:
        self._masked_chars = 0


def _find_mask_cutoff(messages: list, keep_recent: int) -> int:
    """Return the index before which all tool results should be masked."""
    count = 0
    for idx in range(len(messages) - 1, -1, -1):
        msg = messages[idx]
        if msg.get("role") == "assistant" and any(
            "toolUse" in c for c in msg.get("content", [])
        ):
            count += 1
            if count >= keep_recent:
                return idx
    return 0


def _count_tool_turns(messages: list) -> int:
    return sum(
        1
        for m in messages
        if m.get("role") == "assistant"
        and any("toolUse" in c for c in m.get("content", []))
    )
