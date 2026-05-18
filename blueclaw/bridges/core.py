"""Platform-agnostic bridge core: allowlist, per-chat context, message router."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from strands.session.file_session_manager import FileSessionManager

from blueclaw.models import SessionConfig
from blueclaw.runner import next_capture_path, run_turn
from blueclaw.workspace import Workspace

logger = logging.getLogger(__name__)

TELEGRAM_MSG_LIMIT = 4096

_REFUSE_TEMPLATE = (
    "Not authorized. Your chat ID is {chat_id} and your user ID is "
    "{user_id}. Ask the operator to add you to the allowlist."
)


@dataclass
class Allowlist:
    """Authorization gate for messenger bridges.

    Empty chat_ids => refuse everyone (safe default).
    """

    chat_ids: list[int] = field(default_factory=list)
    user_ids: list[int] = field(default_factory=list)

    def authorize(self, *, chat_id: int, user_id: int) -> bool:
        if not self.chat_ids:
            return False
        if chat_id not in self.chat_ids:
            return False
        if self.user_ids and user_id not in self.user_ids:
            return False
        return True


@dataclass
class ChatContext:
    """Per-chat state: own Workspace, asyncio.Lock, sessions dir."""

    chat_id: int
    workspace: Workspace
    sessions_dir: Path
    lock: asyncio.Lock

    @classmethod
    def create(cls, *, chat_id: int, chats_root: Path) -> "ChatContext":
        if not isinstance(chat_id, int) or isinstance(chat_id, bool):
            raise TypeError(f"chat_id must be int, got {type(chat_id).__name__}")
        chat_dir = chats_root / str(chat_id)
        workspace = Workspace(chat_dir)
        # The two <chat_id> segments below mean different things: the outer one
        # (chats_root / <chat_id>) is the workspace identifier — it isolates
        # each Telegram chat's files on disk. The inner one passed to
        # conversation_dir(<chat_id>) is the conversation identifier inside
        # that workspace. They happen to be the same string here because each
        # Telegram chat has exactly one conversation per workspace.
        sessions_dir = workspace.conversation_dir(str(chat_id))
        return cls(
            chat_id=chat_id,
            workspace=workspace,
            sessions_dir=sessions_dir,
            lock=asyncio.Lock(),
        )


class BridgeRouter:
    """Platform-agnostic message router.

    Holds a shared model + config. Lazily builds per-chat ChatContext.
    For each turn: fresh ObserverHooks + Agent, run via asyncio.to_thread,
    return text. Mirrors the lifecycle in blueclaw/server.py.
    """

    def __init__(
        self,
        *,
        config: SessionConfig,
        model: Any,
        allowlist: Allowlist,
        chats_root: Path,
    ) -> None:
        self._config = config
        self._model = model
        self._allowlist = allowlist
        self._chats_root = chats_root
        self._contexts: dict[int, ChatContext] = {}
        self._contexts_lock = asyncio.Lock()

    async def _get_context(self, chat_id: int) -> ChatContext:
        async with self._contexts_lock:
            ctx = self._contexts.get(chat_id)
            if ctx is None:
                ctx = ChatContext.create(chat_id=chat_id, chats_root=self._chats_root)
                self._contexts[chat_id] = ctx
            return ctx

    async def handle_message(self, *, chat_id: int, user_id: int, text: str) -> str:
        if not self._allowlist.authorize(chat_id=chat_id, user_id=user_id):
            return _REFUSE_TEMPLATE.format(chat_id=chat_id, user_id=user_id)

        ctx = await self._get_context(chat_id)
        async with ctx.lock:
            session_manager = FileSessionManager(
                session_id=str(chat_id),
                storage_dir=str(ctx.sessions_dir),
            )
            capture_path = next_capture_path(ctx.workspace.root, str(chat_id))
            outcome = await asyncio.to_thread(
                run_turn,
                self._config,
                ctx.workspace,
                self._model,
                text,
                goal=text,
                source="telegram",
                conversation_id=str(chat_id),
                session_manager=session_manager,
                channel="telegram",
                callback_handler=None,
                scripted=True,
                capture_path=capture_path,
                workspace_root=ctx.workspace.root,
            )
            if outcome.error is not None:
                logger.error(
                    "agent turn failed for chat %s: %s", chat_id, outcome.error
                )
                return f"Agent error: {outcome.error!s}"[:500]
            try:
                ctx.workspace.write_trace(outcome.trace)
                ctx.workspace.append_history(outcome.record)
            except Exception:
                logger.exception("failed to persist trace/history for chat %s", chat_id)
            return outcome.response_text

    async def handle_command(self, *, chat_id: int, user_id: int, command: str) -> str:
        cmd = command.strip().split()[0].lower()
        if cmd.startswith("/"):
            cmd = cmd.split("@", 1)[0]  # strip @botname suffix in groups
        if cmd == "/whoami":
            return f"chat_id={chat_id} user_id={user_id}"
        if cmd == "/start":
            if self._allowlist.authorize(chat_id=chat_id, user_id=user_id):
                return "Hi! You're authorized. Send me a message."
            return _REFUSE_TEMPLATE.format(chat_id=chat_id, user_id=user_id)
        if not self._allowlist.authorize(chat_id=chat_id, user_id=user_id):
            return _REFUSE_TEMPLATE.format(chat_id=chat_id, user_id=user_id)
        if cmd == "/reset":
            ctx = await self._get_context(chat_id)
            history = ctx.workspace.history_path
            if history.exists():
                history.write_text("")
            return "Session history reset. CONTEXT.md kept."
        if cmd == "/forget":
            ctx = await self._get_context(chat_id)
            for p in (ctx.workspace.history_path, ctx.workspace.context_path):
                if p.exists():
                    p.write_text("")
            return "History and CONTEXT.md wiped."
        return f"Unknown command: {cmd}"


def split_for_telegram(text: str, limit: int = TELEGRAM_MSG_LIMIT) -> list[str]:
    """Split text into <=limit chunks, preferring newline boundaries."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        window = remaining[:limit]
        cut = window.rfind("\n")
        if cut <= 0:
            cut = limit
            chunks.append(remaining[:cut])
            remaining = remaining[cut:]
        else:
            chunks.append(remaining[:cut])
            remaining = remaining[cut + 1 :]
    if remaining:
        chunks.append(remaining)
    return chunks
