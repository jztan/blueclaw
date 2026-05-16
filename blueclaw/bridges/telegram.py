"""Telegram adapter for the blueclaw bridge.

python-telegram-bot v22+. Long-polling by default; webhook opt-in.
"""

from __future__ import annotations

import asyncio
import logging
import signal

from telegram import Update
from telegram.constants import ChatAction
from telegram.error import Conflict
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from blueclaw.bridges.core import BridgeRouter, split_for_telegram

logger = logging.getLogger(__name__)

_HEARTBEAT_INTERVAL = 4.0


async def _typing_heartbeat(bot, chat_id: int) -> None:
    try:
        while True:
            await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
            await asyncio.sleep(_HEARTBEAT_INTERVAL)
    except asyncio.CancelledError:
        return


async def _on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if msg is None or msg.text is None:
        return
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id if update.effective_user else 0
    router: BridgeRouter = ctx.application.bot_data["router"]

    heartbeat = asyncio.create_task(_typing_heartbeat(ctx.bot, chat_id))
    try:
        reply = await router.handle_message(
            chat_id=chat_id, user_id=user_id, text=msg.text
        )
    finally:
        heartbeat.cancel()
        try:
            await heartbeat
        except asyncio.CancelledError:
            pass

    await _send_reply(ctx.bot, chat_id, reply)


_EMPTY_REPLY_FALLBACK = (
    "(no reply — the model produced empty output. "
    "Try rephrasing, /reset, or a stronger model.)"
)


async def _send_reply(bot, chat_id: int, reply: str) -> None:
    """Send a reply, substituting a fallback for empty/whitespace text.

    Telegram rejects empty `text` with BadRequest; small Ollama models
    sometimes emit only a tool-use block with no final user-facing text.
    """
    if not reply or not reply.strip():
        reply = _EMPTY_REPLY_FALLBACK
    for chunk in split_for_telegram(reply):
        if not chunk.strip():
            continue
        await bot.send_message(chat_id=chat_id, text=chunk)


async def _on_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if msg is None or msg.text is None:
        return
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id if update.effective_user else 0
    router: BridgeRouter = ctx.application.bot_data["router"]
    reply = await router.handle_command(
        chat_id=chat_id, user_id=user_id, command=msg.text
    )
    await _send_reply(ctx.bot, chat_id, reply)


def build_application(*, bot_token: str, router: BridgeRouter) -> Application:
    """Build a configured PTB Application. Caller drives polling/webhook."""
    app = ApplicationBuilder().token(bot_token).build()
    app.bot_data["router"] = router
    app.add_handler(CommandHandler(["start", "whoami", "reset", "forget"], _on_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _on_message))
    return app


def run_telegram_bridge(
    *,
    bot_token: str,
    router: BridgeRouter,
    mode: str = "polling",
    webhook_url: str | None = None,
    webhook_port: int = 8421,
) -> None:
    """Blocking entry point. Long-polling default, webhook if mode='webhook'."""
    app = build_application(bot_token=bot_token, router=router)
    try:
        if mode == "webhook":
            if not webhook_url:
                raise ValueError("webhook_url required when mode='webhook'")
            app.run_webhook(
                listen="0.0.0.0",
                port=webhook_port,
                webhook_url=webhook_url,
            )
        else:
            app.run_polling(stop_signals=(signal.SIGINT, signal.SIGTERM))
    except Conflict:
        logger.error(
            "Telegram reports another instance is polling this bot token. "
            "Long-polling requires exactly one process per token. Exiting."
        )
        raise SystemExit(2)
