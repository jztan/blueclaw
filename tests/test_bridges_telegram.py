"""Tests for the Telegram adapter."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("telegram")

from blueclaw.bridges.core import split_for_telegram  # noqa: E402
from blueclaw.bridges.telegram import (  # noqa: E402
    _typing_heartbeat,
    build_application,
    run_telegram_bridge,
)


def test_telegram_module_imports():
    assert callable(build_application)
    assert callable(run_telegram_bridge)


def test_splitter_used_for_long_replies():
    long = "x" * 5000
    chunks = split_for_telegram(long)
    assert len(chunks) == 2
    assert sum(len(c) for c in chunks) == 5000


@pytest.mark.asyncio
async def test_heartbeat_cancels_cleanly():
    bot = MagicMock()
    bot.send_chat_action = AsyncMock()
    task = asyncio.create_task(_typing_heartbeat(bot, chat_id=1))
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert bot.send_chat_action.await_count >= 1
