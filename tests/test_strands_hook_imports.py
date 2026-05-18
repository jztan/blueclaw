# tests/test_strands_hook_imports.py
"""CI smoke test — fail loudly if Strands renames or removes our hook events.

Phase 1 of trace UI Conversation-First Observability depends on:
- BeforeToolCallEvent, AfterToolCallEvent (already in use)
- BeforeModelCallEvent, AfterModelCallEvent (NEW — Strands 1.39+ names)
- MessageAddedEvent (NEW)

A Strands upgrade that renames any of these would silently skip the
events.jsonl emission for that hook. This test catches the rename at
import time so the upgrade PR cannot pass CI.
"""


def test_required_hook_events_importable() -> None:
    from strands.hooks import (
        AfterModelCallEvent,
        AfterToolCallEvent,
        BeforeModelCallEvent,
        BeforeToolCallEvent,
        HookProvider,
        HookRegistry,
        MessageAddedEvent,
    )

    # Touch each symbol so static analysis can't elide the import
    for cls in (
        BeforeToolCallEvent,
        AfterToolCallEvent,
        BeforeModelCallEvent,
        AfterModelCallEvent,
        MessageAddedEvent,
        HookProvider,
        HookRegistry,
    ):
        assert cls.__name__
