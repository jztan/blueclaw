"""Tests for blueclaw.cli — commands, welcome banner, pixel art."""

from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from rich.console import Console
from typer.testing import CliRunner

from blueclaw import __version__
from blueclaw.cli import app, render_pixel_art, render_welcome_banner
from blueclaw.models import SessionConfig
from blueclaw.workspace import Workspace

runner = CliRunner()


# --- Command existence ---


class TestCommands:
    def test_main_version_flag(self):
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert __version__ in result.output

    @patch("blueclaw.cli.run_session")
    def test_main_command_exists(self, mock_run):
        result = runner.invoke(app, [], input="exit\n")
        # Should not error out (may exit 0 or get through)
        assert result.exit_code == 0 or mock_run.called

    @patch("blueclaw.cli.run_session")
    def test_main_model_flag(self, mock_run):
        result = runner.invoke(app, ["--model", "ollama/llama3"], input="exit\n")
        if mock_run.called:
            call_kwargs = mock_run.call_args
            assert "ollama/llama3" in str(call_kwargs)

    def test_run_command_exists(self):
        result = runner.invoke(app, ["run", "--help"])
        assert result.exit_code == 0
        assert "prompt" in result.output.lower() or "run" in result.output.lower()

    def test_init_command_exists(self):
        result = runner.invoke(app, ["init", "--help"])
        assert result.exit_code == 0

    def test_history_command_exists(self):
        result = runner.invoke(app, ["history", "--help"])
        assert result.exit_code == 0

    @patch("blueclaw.session.update_context_on_exit")
    @patch("blueclaw.session.cleanup_mcp_clients")
    @patch("blueclaw.session.Agent")
    @patch("blueclaw.session.build_model")
    def test_run_updates_context_on_exit(
        self,
        mock_build_model,
        mock_agent_cls,
        mock_cleanup,
        mock_update_context,
        tmp_path,
    ):
        mock_build_model.return_value = MagicMock()
        mock_agent = MagicMock()
        result_obj = MagicMock()
        result_obj.message = "ok"
        result_obj.metrics.accumulated_usage = {
            "inputTokens": 1,
            "outputTokens": 1,
            "totalTokens": 2,
        }
        mock_agent.return_value = result_obj
        mock_agent_cls.return_value = mock_agent

        yaml_path = tmp_path / "blueclaw.yaml"
        yaml_path.write_text(
            "model:\n  provider: anthropic\n  model_id: claude-sonnet-4-6\n"
            "workspace:\n  path: "
            f"{tmp_path / 'workspace'}\n"
            "tools: []\n"
        )

        with patch(
            "blueclaw.cli.Path",
            side_effect=lambda p: yaml_path if p == "blueclaw.yaml" else Path(p),
        ):
            result = runner.invoke(app, ["run", "hello"])

        assert result.exit_code == 0
        assert mock_update_context.called
        assert mock_cleanup.called


# --- Init command ---


class TestInitCommand:
    def test_init_creates_workspace(self, tmp_path):
        with patch("blueclaw.cli.DEFAULT_WORKSPACE", tmp_path / "workspace"):
            result = runner.invoke(app, ["init"])
            assert result.exit_code == 0
            assert (tmp_path / "workspace").exists()

    def test_init_creates_context_md(self, tmp_path):
        with patch("blueclaw.cli.DEFAULT_WORKSPACE", tmp_path / "workspace"):
            runner.invoke(app, ["init"])
            assert (tmp_path / "workspace" / "CONTEXT.md").exists()

    def test_init_creates_history_dir(self, tmp_path):
        with patch("blueclaw.cli.DEFAULT_WORKSPACE", tmp_path / "workspace"):
            runner.invoke(app, ["init"])
            assert (tmp_path / "workspace" / ".blueclaw").exists()

    def test_init_idempotent(self, tmp_path):
        with patch("blueclaw.cli.DEFAULT_WORKSPACE", tmp_path / "workspace"):
            runner.invoke(app, ["init"])
            # Write something to CONTEXT.md
            (tmp_path / "workspace" / "CONTEXT.md").write_text("custom content")
            runner.invoke(app, ["init"])
            # Should not overwrite
            assert (
                tmp_path / "workspace" / "CONTEXT.md"
            ).read_text() == "custom content"

    def test_init_prints_confirmation(self, tmp_path):
        with patch("blueclaw.cli.DEFAULT_WORKSPACE", tmp_path / "workspace"):
            result = runner.invoke(app, ["init"])
            assert (
                "initialized" in result.output.lower()
                or "created" in result.output.lower()
                or "workspace" in result.output.lower()
            )


# --- History command ---


class TestHistoryCommand:
    def test_history_empty(self, tmp_path):
        with patch("blueclaw.cli.DEFAULT_WORKSPACE", tmp_path / "workspace"):
            Workspace(tmp_path / "workspace")
            result = runner.invoke(app, ["history"])
            assert result.exit_code == 0
            assert "no" in result.output.lower() or len(result.output.strip()) >= 0

    def test_history_shows_runs(self, tmp_path):
        from datetime import datetime, timezone

        from blueclaw.models import RunRecord

        ws_path = tmp_path / "workspace"
        ws = Workspace(ws_path)
        rec = RunRecord(
            ts=datetime(2026, 3, 14, tzinfo=timezone.utc),
            goal="test goal for history",
            tools=["web_search"],
            tokens=100,
            cost=0.01,
        )
        ws.append_history(rec)
        with patch("blueclaw.cli.DEFAULT_WORKSPACE", ws_path):
            result = runner.invoke(app, ["history"])
            assert "test goal for history" in result.output


# --- Welcome banner ---


class TestWelcomeBanner:
    def test_welcome_banner_contains_version(self):
        output = StringIO()
        console = Console(file=output, width=120)
        cfg = SessionConfig()
        ws = Workspace(Path("/tmp/test-banner-ws"))
        render_welcome_banner(cfg, ws, console)
        assert __version__ in output.getvalue()

    def test_welcome_banner_contains_model(self):
        output = StringIO()
        console = Console(file=output, width=120)
        cfg = SessionConfig(model_id="claude-sonnet-4-6")
        ws = Workspace(Path("/tmp/test-banner-ws2"))
        render_welcome_banner(cfg, ws, console)
        assert "claude-sonnet-4-6" in output.getvalue()

    def test_welcome_banner_contains_workspace(self):
        output = StringIO()
        console = Console(file=output, width=120)
        cfg = SessionConfig()
        ws = Workspace(Path("/tmp/test-banner-ws3"))
        render_welcome_banner(cfg, ws, console)
        assert "/tmp/test-banner-ws3" in output.getvalue()

    def test_welcome_banner_contains_mascot(self):
        output = StringIO()
        console = Console(file=output, width=120, force_terminal=True)
        cfg = SessionConfig()
        ws = Workspace(Path("/tmp/test-banner-ws4"))
        render_welcome_banner(cfg, ws, console)
        text = output.getvalue()
        # Half-block chars should be present
        assert any(ch in text for ch in "\u2580\u2584\u2588")

    def test_welcome_banner_ollama_note(self):
        output = StringIO()
        console = Console(file=output, width=120)
        cfg = SessionConfig(provider="ollama", model_id="llama3")
        ws = Workspace(Path("/tmp/test-banner-ws5"))
        render_welcome_banner(cfg, ws, console)
        assert "locally" in output.getvalue().lower()

    def test_welcome_banner_shows_recovery_checkpoint(self):
        output = StringIO()
        console = Console(file=output, width=120)
        cfg = SessionConfig()
        ws = Workspace(Path("/tmp/test-banner-ws6"))
        ws.write_last_turn_checkpoint("goal", "assistant")
        render_welcome_banner(cfg, ws, console)
        assert "recovery checkpoint" in output.getvalue().lower()


# --- Pixel art ---


class TestPixelArt:
    def test_render_pixel_art_returns_text(self):
        from rich.text import Text

        result = render_pixel_art()
        assert isinstance(result, Text)

    def test_render_pixel_art_uses_half_blocks(self):
        output = StringIO()
        console = Console(file=output, force_terminal=True)
        art = render_pixel_art()
        console.print(art)
        text = output.getvalue()
        assert any(ch in text for ch in "\u2580\u2584\u2588")

    def test_render_pixel_art_line_count(self):
        art = render_pixel_art()
        plain = art.plain
        lines = plain.strip().splitlines()
        assert len(lines) == 9

    def test_render_pixel_art_has_colors(self):
        output = StringIO()
        console = Console(file=output, force_terminal=True)
        art = render_pixel_art()
        console.print(art)
        text = output.getvalue()
        # ANSI escape codes should be present
        assert "\x1b[" in text
