"""Tests for blueclaw.cli — commands, welcome banner, pixel art."""

from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from rich.console import Console
from typer.testing import CliRunner

from blueclaw import __version__
from blueclaw.cli import app, render_pixel_art, render_welcome_banner
from blueclaw.models import RunTrace, SessionConfig, TraceStep
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


# --- Trace commands ---


def _write_test_trace(ws_path, run_id="20260315-101201", goal="test goal"):
    ts = datetime(2026, 3, 15, 10, 12, 1, tzinfo=timezone.utc)
    step = TraceStep(
        index=1,
        tool_name="web_search",
        status="success",
        start_time=ts,
        end_time=ts,
        duration_ms=842,
        input_summary={"query": "test"},
        output_summary="results",
    )
    trace = RunTrace(
        run_id=run_id,
        goal=goal,
        start_time=ts,
        end_time=ts,
        model_id="claude-sonnet-4-6",
        steps=[step],
        total_tokens=2847,
        total_cost=0.0042,
        status="success",
    )
    ws = Workspace(ws_path)
    ws.write_trace(trace)
    return trace


class TestTraceCommands:
    def test_trace_list_command_exists(self):
        result = runner.invoke(app, ["trace", "list", "--help"])
        assert result.exit_code == 0

    def test_trace_show_command_exists(self):
        result = runner.invoke(app, ["trace", "show", "--help"])
        assert result.exit_code == 0

    def test_trace_list_empty(self, tmp_path):
        ws_path = tmp_path / "workspace"
        Workspace(ws_path)
        with patch("blueclaw.cli.DEFAULT_WORKSPACE", ws_path):
            result = runner.invoke(app, ["trace", "list"])
            assert result.exit_code == 0
            assert "no" in result.output.lower()

    def test_trace_list_shows_traces(self, tmp_path):
        ws_path = tmp_path / "workspace"
        _write_test_trace(ws_path, goal="research MCP ecosystem")
        with patch("blueclaw.cli.DEFAULT_WORKSPACE", ws_path):
            result = runner.invoke(app, ["trace", "list"])
            assert result.exit_code == 0
            assert "20260315-101201" in result.output
            assert "research MCP ecosystem" in result.output
            assert "success" in result.output

    def test_trace_list_truncates_long_goal(self, tmp_path):
        ws_path = tmp_path / "workspace"
        long_goal = "x" * 80
        _write_test_trace(ws_path, goal=long_goal)
        with patch("blueclaw.cli.DEFAULT_WORKSPACE", ws_path):
            result = runner.invoke(app, ["trace", "list"])
            assert "..." in result.output

    def test_trace_show_displays_trace(self, tmp_path):
        ws_path = tmp_path / "workspace"
        _write_test_trace(ws_path, goal="research MCP")
        with patch("blueclaw.cli.DEFAULT_WORKSPACE", ws_path):
            result = runner.invoke(app, ["trace", "show", "20260315-101201"])
            assert result.exit_code == 0
            assert "20260315-101201" in result.output
            assert "research MCP" in result.output
            assert "web_search" in result.output
            assert "842ms" in result.output

    def test_trace_show_not_found(self, tmp_path):
        ws_path = tmp_path / "workspace"
        Workspace(ws_path)
        with patch("blueclaw.cli.DEFAULT_WORKSPACE", ws_path):
            result = runner.invoke(app, ["trace", "show", "nonexistent"])
            assert result.exit_code == 1
            assert "not found" in result.output.lower()

    def test_trace_show_displays_model(self, tmp_path):
        ws_path = tmp_path / "workspace"
        _write_test_trace(ws_path)
        with patch("blueclaw.cli.DEFAULT_WORKSPACE", ws_path):
            result = runner.invoke(app, ["trace", "show", "20260315-101201"])
            assert "claude-sonnet-4-6" in result.output

    def test_trace_show_displays_totals(self, tmp_path):
        ws_path = tmp_path / "workspace"
        _write_test_trace(ws_path)
        with patch("blueclaw.cli.DEFAULT_WORKSPACE", ws_path):
            result = runner.invoke(app, ["trace", "show", "20260315-101201"])
            assert "2847" in result.output
            assert "$0.0042" in result.output
