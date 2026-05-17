"""Tests for blueclaw.bridges.core and TelegramBridgeConfig."""

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from blueclaw.models import TelegramBridgeConfig


def test_telegram_config_defaults():
    cfg = TelegramBridgeConfig(bot_token="abc")
    assert cfg.allowed_chat_ids == []
    assert cfg.allowed_user_ids == []
    assert cfg.mode == "polling"
    assert cfg.webhook_url is None
    assert cfg.webhook_port == 8421
    assert cfg.chats_root.name == "chats"


def test_telegram_config_rejects_bad_mode():
    with pytest.raises(ValueError):
        TelegramBridgeConfig(bot_token="abc", mode="bogus")


def test_telegram_config_expands_chats_root():
    cfg = TelegramBridgeConfig(bot_token="abc", chats_root="~/foo")
    assert str(cfg.chats_root).startswith("/")
    assert cfg.chats_root.name == "foo"


# --- Allowlist -----------------------------------------------------------


def test_allowlist_empty_refuses_everyone():
    from blueclaw.bridges.core import Allowlist

    al = Allowlist(chat_ids=[], user_ids=[])
    assert al.authorize(chat_id=123, user_id=456) is False


def test_allowlist_chat_id_allowed():
    from blueclaw.bridges.core import Allowlist

    al = Allowlist(chat_ids=[123], user_ids=[])
    assert al.authorize(chat_id=123, user_id=999) is True
    assert al.authorize(chat_id=124, user_id=999) is False


def test_allowlist_user_id_required_when_set():
    from blueclaw.bridges.core import Allowlist

    al = Allowlist(chat_ids=[123], user_ids=[42])
    assert al.authorize(chat_id=123, user_id=42) is True
    assert al.authorize(chat_id=123, user_id=43) is False


def test_allowlist_user_id_only():
    from blueclaw.bridges.core import Allowlist

    al = Allowlist(chat_ids=[], user_ids=[42])
    assert al.authorize(chat_id=999, user_id=42) is False


# --- ChatContext ---------------------------------------------------------


def test_chat_context_paths(tmp_path: Path):
    from blueclaw.bridges.core import ChatContext

    ctx = ChatContext.create(chat_id=42, chats_root=tmp_path)
    assert ctx.chat_id == 42
    assert ctx.workspace.root == tmp_path / "42"
    assert ctx.workspace.history_path.parent.name == ".blueclaw"
    sessions_dir = tmp_path / "42" / ".blueclaw" / "sessions"
    assert sessions_dir.exists()


def test_chat_context_negative_id_for_groups(tmp_path: Path):
    from blueclaw.bridges.core import ChatContext

    ctx = ChatContext.create(chat_id=-100200300, chats_root=tmp_path)
    assert ctx.workspace.root == tmp_path / "-100200300"


def test_chat_context_rejects_non_int(tmp_path: Path):
    from blueclaw.bridges.core import ChatContext

    with pytest.raises(TypeError):
        ChatContext.create(  # type: ignore[arg-type]
            chat_id="../etc/passwd", chats_root=tmp_path
        )


def test_chat_context_lock_is_asyncio_lock(tmp_path: Path):
    from blueclaw.bridges.core import ChatContext

    ctx = ChatContext.create(chat_id=42, chats_root=tmp_path)
    assert isinstance(ctx.lock, asyncio.Lock)


# --- BridgeRouter --------------------------------------------------------


def _make_router(tmp_path: Path, allow_chat=None):
    from blueclaw.bridges.core import Allowlist, BridgeRouter
    from blueclaw.models import SessionConfig

    config = SessionConfig()
    return BridgeRouter(
        config=config,
        model=object(),
        allowlist=Allowlist(chat_ids=allow_chat or []),
        chats_root=tmp_path,
    )


@pytest.mark.asyncio
async def test_router_refuses_unauthorized(tmp_path: Path):
    router = _make_router(tmp_path, allow_chat=[])
    reply = await router.handle_message(chat_id=42, user_id=42, text="hi")
    assert "Not authorized" in reply
    assert "42" in reply


def _stub_outcome(response_text: str = "hello back", error: Exception | None = None):
    from blueclaw.runner import RunOutcome

    return RunOutcome(
        result=MagicMock() if error is None else None,
        agent=MagicMock(),
        response_text="" if error is not None else response_text,
        trace=MagicMock() if error is None else None,
        record=MagicMock() if error is None else None,
        capture_errors=[],
        error=error,
    )


@pytest.mark.asyncio
async def test_router_routes_authorized_to_agent(tmp_path: Path):
    router = _make_router(tmp_path, allow_chat=[42])

    with (
        patch(
            "blueclaw.bridges.core.run_turn", return_value=_stub_outcome("hello back")
        ) as mock_run_turn,
        patch("blueclaw.bridges.core.FileSessionManager"),
    ):
        reply = await router.handle_message(chat_id=42, user_id=42, text="ping")

    assert reply == "hello back"
    mock_run_turn.assert_called_once()
    kwargs = mock_run_turn.call_args.kwargs
    assert kwargs["source"] == "telegram"
    assert kwargs["conversation_id"] == "42"
    assert kwargs["channel"] == "telegram"
    assert kwargs["callback_handler"] is None
    assert kwargs["capture_path"] is None
    assert kwargs["goal"] == "ping"
    # text passes as the 4th positional (agent_input)
    assert mock_run_turn.call_args.args[3] == "ping"


@pytest.mark.asyncio
async def test_router_creates_per_chat_workspace(tmp_path: Path):
    router = _make_router(tmp_path, allow_chat=[1, 2])
    with (
        patch("blueclaw.bridges.core.run_turn", return_value=_stub_outcome("ok")),
        patch("blueclaw.bridges.core.FileSessionManager"),
    ):
        await router.handle_message(chat_id=1, user_id=1, text="x")
        await router.handle_message(chat_id=2, user_id=2, text="y")

    assert (tmp_path / "1").is_dir()
    assert (tmp_path / "2").is_dir()
    assert (tmp_path / "1" / ".blueclaw" / "sessions").is_dir()
    assert (tmp_path / "2" / ".blueclaw" / "sessions").is_dir()


@pytest.mark.asyncio
async def test_router_returns_error_reply_on_agent_failure(tmp_path: Path):
    router = _make_router(tmp_path, allow_chat=[42])
    ctx = await router._get_context(42)
    write_trace = MagicMock()
    append_history = MagicMock()
    ctx.workspace.write_trace = write_trace
    ctx.workspace.append_history = append_history

    with (
        patch(
            "blueclaw.bridges.core.run_turn",
            return_value=_stub_outcome(error=RuntimeError("boom")),
        ),
        patch("blueclaw.bridges.core.FileSessionManager"),
    ):
        reply = await router.handle_message(chat_id=42, user_id=42, text="ping")

    assert reply.startswith("Agent error: ")
    assert "boom" in reply
    assert len(reply) <= 500
    write_trace.assert_not_called()
    append_history.assert_not_called()


@pytest.mark.asyncio
async def test_router_reset_command(tmp_path: Path):
    router = _make_router(tmp_path, allow_chat=[42])
    chat_dir = tmp_path / "42"
    chat_dir.mkdir()
    (chat_dir / ".blueclaw").mkdir()
    history = chat_dir / ".blueclaw" / "history.jsonl"
    history.write_text('{"x": 1}\n')

    reply = await router.handle_command(chat_id=42, user_id=42, command="/reset")
    assert "reset" in reply.lower()
    assert history.read_text() == ""


@pytest.mark.asyncio
async def test_router_whoami_works_unauthorized(tmp_path: Path):
    router = _make_router(tmp_path, allow_chat=[])
    reply = await router.handle_command(chat_id=42, user_id=99, command="/whoami")
    assert "42" in reply and "99" in reply


# --- split_for_telegram --------------------------------------------------


def test_split_short_message_passes_through():
    from blueclaw.bridges.core import split_for_telegram

    assert split_for_telegram("hello") == ["hello"]


def test_split_at_4096_boundary():
    from blueclaw.bridges.core import split_for_telegram

    msg = ("a" * 4096) + "b"
    chunks = split_for_telegram(msg)
    assert len(chunks) == 2
    assert all(len(c) <= 4096 for c in chunks)


def test_split_prefers_newline_boundary():
    from blueclaw.bridges.core import split_for_telegram

    line = "x" * 100
    msg = "\n".join([line] * 60)
    chunks = split_for_telegram(msg)
    assert len(chunks) >= 2
    assert all(len(c) <= 4096 for c in chunks)
    for c in chunks[:-1]:
        assert c.endswith(line)


# --- SessionConfig bridges field ---------------------------------------


def test_session_config_carries_bridges_block():
    from blueclaw.models import SessionConfig

    cfg = SessionConfig(bridges={"telegram": {"bot_token": "abc"}})
    assert cfg.bridges["telegram"]["bot_token"] == "abc"
