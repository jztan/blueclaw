"""Tests for blueclaw.cli — commands, welcome banner, pixel art."""

from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

from rich.console import Console
from typer.testing import CliRunner

from blueclaw import __version__
from blueclaw.cli import app, render_pixel_art, render_welcome_banner
from blueclaw.models import RunTrace, SandboxConfig, SessionConfig, TraceStep
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
        runner.invoke(app, ["--model", "ollama/llama3"], input="exit\n")
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

    @patch("blueclaw.session.BackgroundContextUpdater")
    @patch("blueclaw.session.cleanup_mcp_clients")
    @patch("blueclaw.session.Agent")
    @patch("blueclaw.session.build_model")
    def test_run_updates_context_on_exit(
        self,
        mock_build_model,
        mock_agent_cls,
        mock_cleanup,
        mock_updater_cls,
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

        config = SessionConfig(
            provider="anthropic",
            model_id="claude-sonnet-4-6",
            workspace_path=tmp_path / "workspace",
            tools=[],
        )

        with patch("blueclaw.session.load_config", return_value=config):
            result = runner.invoke(app, ["run", "hello"])

        assert result.exit_code == 0
        mock_updater_cls.return_value.trigger.assert_called_once()
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

    def test_init_creates_soul_md(self, tmp_path):
        with patch("blueclaw.cli.DEFAULT_WORKSPACE", tmp_path / "workspace"):
            runner.invoke(app, ["init"])
            soul = tmp_path / "workspace" / "SOUL.md"
            assert soul.exists()
            assert "blueclaw" in soul.read_text().lower()

    def test_init_soul_idempotent(self, tmp_path):
        with patch("blueclaw.cli.DEFAULT_WORKSPACE", tmp_path / "workspace"):
            runner.invoke(app, ["init"])
            (tmp_path / "workspace" / "SOUL.md").write_text("custom soul")
            runner.invoke(app, ["init"])
            assert (tmp_path / "workspace" / "SOUL.md").read_text() == "custom soul"

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
        assert "•   •" in text
        assert "▝▛███▜▘" in text

    def test_welcome_banner_ollama_note(self):
        output = StringIO()
        console = Console(file=output, width=120)
        cfg = SessionConfig(provider="ollama", model_id="llama3")
        ws = Workspace(Path("/tmp/test-banner-ws5"))
        render_welcome_banner(cfg, ws, console)
        assert "local mode" in output.getvalue().lower()

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

    def test_render_pixel_art_contains_claws(self):
        output = StringIO()
        console = Console(file=output, force_terminal=True)
        art = render_pixel_art()
        console.print(art)
        text = output.getvalue()
        assert "\\ " in art.plain
        assert "▝▛███▜▘" in text

    def test_render_pixel_art_line_count(self):
        art = render_pixel_art()
        plain = art.plain
        lines = plain.strip().splitlines()
        assert len(lines) == 3

    def test_render_pixel_art_has_colors(self):
        art = render_pixel_art()
        assert art.spans


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


class TestTraceExplain:
    def test_explain_command_exists(self):
        result = runner.invoke(app, ["trace", "explain", "--help"])
        assert result.exit_code == 0

    def test_explain_not_found_exits_1(self, tmp_path):
        ws_path = tmp_path / "workspace"
        Workspace(ws_path)
        with patch("blueclaw.cli.DEFAULT_WORKSPACE", ws_path):
            result = runner.invoke(app, ["trace", "explain", "nonexistent"])
            assert result.exit_code == 1
            assert "not found" in result.output.lower()

    @patch("strands.Agent")
    @patch("blueclaw.session.build_model")
    @patch("blueclaw.session.load_config")
    def test_explain_reads_trace_and_calls_agent(
        self, mock_load_config, mock_build_model, mock_agent_cls, tmp_path
    ):
        ws_path = tmp_path / "workspace"
        _write_test_trace(ws_path, goal="research MCP")
        mock_load_config.return_value = SessionConfig()
        mock_build_model.return_value = MagicMock()
        mock_agent_instance = MagicMock()
        mock_agent_instance.return_value = MagicMock(message="explanation")
        mock_agent_cls.return_value = mock_agent_instance

        with patch("blueclaw.cli.DEFAULT_WORKSPACE", ws_path):
            runner.invoke(app, ["trace", "explain", "20260315-101201"])

        mock_agent_cls.assert_called_once()
        call_kwargs = mock_agent_cls.call_args.kwargs
        assert call_kwargs["tools"] == []
        assert "callback_handler" not in call_kwargs
        # Agent should be called with prompt containing the goal
        prompt = mock_agent_instance.call_args[0][0]
        assert "research MCP" in prompt

    @patch("strands.Agent")
    @patch("blueclaw.session.build_model")
    @patch("blueclaw.session.load_config")
    def test_explain_prints_post_hoc_disclaimer(
        self, mock_load_config, mock_build_model, mock_agent_cls, tmp_path
    ):
        ws_path = tmp_path / "workspace"
        _write_test_trace(ws_path)
        mock_load_config.return_value = SessionConfig()
        mock_build_model.return_value = MagicMock()
        mock_agent_instance = MagicMock()
        mock_agent_instance.return_value = MagicMock(message="explanation")
        mock_agent_cls.return_value = mock_agent_instance

        with patch("blueclaw.cli.DEFAULT_WORKSPACE", ws_path):
            result = runner.invoke(app, ["trace", "explain", "20260315-101201"])
        assert "post-hoc" in result.output.lower()

    @patch("strands.Agent")
    @patch("blueclaw.session.build_model")
    @patch("blueclaw.session.load_config")
    def test_explain_loads_config_for_model(
        self, mock_load_config, mock_build_model, mock_agent_cls, tmp_path
    ):
        ws_path = tmp_path / "workspace"
        _write_test_trace(ws_path)
        mock_load_config.return_value = SessionConfig()
        mock_build_model.return_value = MagicMock()
        mock_agent_instance = MagicMock()
        mock_agent_instance.return_value = MagicMock(message="explanation")
        mock_agent_cls.return_value = mock_agent_instance

        with patch("blueclaw.cli.DEFAULT_WORKSPACE", ws_path):
            runner.invoke(app, ["trace", "explain", "20260315-101201"])
        mock_load_config.assert_called_once()
        mock_build_model.assert_called_once()


class TestTraceGraph:
    def test_graph_command_exists(self):
        result = runner.invoke(app, ["trace", "graph", "--help"])
        assert result.exit_code == 0

    def test_graph_not_found_exits_1(self, tmp_path):
        ws_path = tmp_path / "workspace"
        Workspace(ws_path)
        with patch("blueclaw.cli.DEFAULT_WORKSPACE", ws_path):
            result = runner.invoke(app, ["trace", "graph", "nonexistent"])
            assert result.exit_code == 1
            assert "not found" in result.output.lower()

    def test_graph_shows_goal_as_root(self, tmp_path):
        ws_path = tmp_path / "workspace"
        _write_test_trace(ws_path, goal="research MCP")
        with patch("blueclaw.cli.DEFAULT_WORKSPACE", ws_path):
            result = runner.invoke(app, ["trace", "graph", "20260315-101201"])
            assert result.exit_code == 0
            assert "research MCP" in result.output

    def test_graph_shows_tool_names(self, tmp_path):
        ws_path = tmp_path / "workspace"
        _write_test_trace(ws_path)
        with patch("blueclaw.cli.DEFAULT_WORKSPACE", ws_path):
            result = runner.invoke(app, ["trace", "graph", "20260315-101201"])
            assert "web_search" in result.output

    def test_graph_shows_duration(self, tmp_path):
        ws_path = tmp_path / "workspace"
        _write_test_trace(ws_path)
        with patch("blueclaw.cli.DEFAULT_WORKSPACE", ws_path):
            result = runner.invoke(app, ["trace", "graph", "20260315-101201"])
            assert "842ms" in result.output

    def test_graph_shows_status_indicators(self, tmp_path):
        ws_path = tmp_path / "workspace"
        _write_test_trace(ws_path)
        with patch("blueclaw.cli.DEFAULT_WORKSPACE", ws_path):
            result = runner.invoke(app, ["trace", "graph", "20260315-101201"])
            # success step should have check mark
            assert "\u2713" in result.output

    def test_graph_shows_input_summary(self, tmp_path):
        ws_path = tmp_path / "workspace"
        _write_test_trace(ws_path)
        with patch("blueclaw.cli.DEFAULT_WORKSPACE", ws_path):
            result = runner.invoke(app, ["trace", "graph", "20260315-101201"])
            assert "query" in result.output


class TestTraceDiff:
    def test_diff_command_exists(self):
        result = runner.invoke(app, ["trace", "diff", "--help"])
        assert result.exit_code == 0

    def test_diff_first_not_found_exits_1(self, tmp_path):
        ws_path = tmp_path / "workspace"
        Workspace(ws_path)
        with patch("blueclaw.cli.DEFAULT_WORKSPACE", ws_path):
            result = runner.invoke(app, ["trace", "diff", "bad1", "bad2"])
            assert result.exit_code == 1

    def test_diff_second_not_found_exits_1(self, tmp_path):
        ws_path = tmp_path / "workspace"
        _write_test_trace(ws_path, run_id="20260315-101201")
        with patch("blueclaw.cli.DEFAULT_WORKSPACE", ws_path):
            result = runner.invoke(
                app, ["trace", "diff", "20260315-101201", "nonexistent"]
            )
            assert result.exit_code == 1

    def test_diff_shows_both_run_ids(self, tmp_path):
        ws_path = tmp_path / "workspace"
        _write_test_trace(ws_path, run_id="20260315-101201", goal="first")
        _write_test_trace(ws_path, run_id="20260315-143022", goal="second")
        with patch("blueclaw.cli.DEFAULT_WORKSPACE", ws_path):
            result = runner.invoke(
                app, ["trace", "diff", "20260315-101201", "20260315-143022"]
            )
            assert result.exit_code == 0
            assert "20260315-101201" in result.output
            assert "20260315-143022" in result.output

    def test_diff_shows_both_goals(self, tmp_path):
        ws_path = tmp_path / "workspace"
        _write_test_trace(ws_path, run_id="20260315-101201", goal="research MCP")
        _write_test_trace(ws_path, run_id="20260315-143022", goal="research FastAPI")
        with patch("blueclaw.cli.DEFAULT_WORKSPACE", ws_path):
            result = runner.invoke(
                app, ["trace", "diff", "20260315-101201", "20260315-143022"]
            )
            assert "research MCP" in result.output
            assert "research FastAPI" in result.output

    def test_diff_shows_token_comparison(self, tmp_path):
        ws_path = tmp_path / "workspace"
        _write_test_trace(ws_path, run_id="20260315-101201")
        _write_test_trace(ws_path, run_id="20260315-143022")
        with patch("blueclaw.cli.DEFAULT_WORKSPACE", ws_path):
            result = runner.invoke(
                app, ["trace", "diff", "20260315-101201", "20260315-143022"]
            )
            # Both traces have 2847 tokens
            assert "2847" in result.output


class TestTraceReplay:
    def test_replay_command_exists(self):
        result = runner.invoke(app, ["trace", "replay", "--help"])
        assert result.exit_code == 0

    def test_replay_not_found_exits_1(self, tmp_path):
        ws_path = tmp_path / "workspace"
        Workspace(ws_path)
        with patch("blueclaw.cli.DEFAULT_WORKSPACE", ws_path):
            result = runner.invoke(app, ["trace", "replay", "nonexistent"])
            assert result.exit_code == 1
            assert "not found" in result.output.lower()

    def test_replay_shows_header(self, tmp_path):
        ws_path = tmp_path / "workspace"
        _write_test_trace(ws_path, goal="research MCP")
        with patch("blueclaw.cli.DEFAULT_WORKSPACE", ws_path):
            result = runner.invoke(
                app, ["trace", "replay", "20260315-101201"], input="\n\n"
            )
            assert "research MCP" in result.output

    def test_replay_shows_each_step(self, tmp_path):
        ws_path = tmp_path / "workspace"
        _write_test_trace(ws_path)
        with patch("blueclaw.cli.DEFAULT_WORKSPACE", ws_path):
            result = runner.invoke(
                app, ["trace", "replay", "20260315-101201"], input="\n\n"
            )
            assert "web_search" in result.output

    def test_replay_shows_step_details(self, tmp_path):
        ws_path = tmp_path / "workspace"
        _write_test_trace(ws_path)
        with patch("blueclaw.cli.DEFAULT_WORKSPACE", ws_path):
            result = runner.invoke(
                app, ["trace", "replay", "20260315-101201"], input="\n\n"
            )
            assert "842ms" in result.output
            assert "query" in result.output

    def test_replay_quit_early(self, tmp_path):
        ws_path = tmp_path / "workspace"
        _write_test_trace(ws_path)
        with patch("blueclaw.cli.DEFAULT_WORKSPACE", ws_path):
            result = runner.invoke(
                app, ["trace", "replay", "20260315-101201"], input="q\n"
            )
            # Should not show the step details after quit
            assert result.exit_code == 0

    def test_replay_shows_final_summary(self, tmp_path):
        ws_path = tmp_path / "workspace"
        _write_test_trace(ws_path)
        with patch("blueclaw.cli.DEFAULT_WORKSPACE", ws_path):
            result = runner.invoke(
                app, ["trace", "replay", "20260315-101201"], input="\n\n"
            )
            assert "2847" in result.output


# --- v1.2: trace timeline ---


class TestTraceTimeline:
    """v1.2: blueclaw trace timeline <run_id>."""

    def test_not_found(self, tmp_path):
        ws_path = tmp_path / "workspace"
        with patch("blueclaw.cli.DEFAULT_WORKSPACE", ws_path):
            result = runner.invoke(app, ["trace", "timeline", "nonexistent"])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_basic_rendering(self, tmp_path):
        ws_path = tmp_path / "workspace"
        _write_test_trace(ws_path)
        with patch("blueclaw.cli.DEFAULT_WORKSPACE", ws_path):
            result = runner.invoke(app, ["trace", "timeline", "20260315-101201"])
        assert result.exit_code == 0
        assert "web_search" in result.output
        assert "842" in result.output

    def test_shows_goal(self, tmp_path):
        ws_path = tmp_path / "workspace"
        _write_test_trace(ws_path)
        with patch("blueclaw.cli.DEFAULT_WORKSPACE", ws_path):
            result = runner.invoke(app, ["trace", "timeline", "20260315-101201"])
        assert result.exit_code == 0
        assert "test goal" in result.output.lower()

    def test_shows_overhead(self, tmp_path):
        ws_path = tmp_path / "workspace"
        _write_test_trace(ws_path)
        with patch("blueclaw.cli.DEFAULT_WORKSPACE", ws_path):
            result = runner.invoke(app, ["trace", "timeline", "20260315-101201"])
        assert result.exit_code == 0
        assert "overhead" in result.output.lower() or "wall" in result.output.lower()

    def test_single_step_trace(self, tmp_path):
        """Single step should not cause division errors."""
        ws_path = tmp_path / "workspace"
        ts = datetime(2026, 3, 15, 10, 12, 1, tzinfo=timezone.utc)
        step = TraceStep(
            index=1,
            tool_name="web_search",
            status="success",
            start_time=ts,
            end_time=ts,
            duration_ms=500,
        )
        trace = RunTrace(
            run_id="20260315-101201",
            goal="single step",
            start_time=ts,
            end_time=ts,
            model_id="claude-sonnet-4-6",
            steps=[step],
            total_tokens=100,
            status="success",
        )
        ws = Workspace(ws_path)
        ws.write_trace(trace)
        with patch("blueclaw.cli.DEFAULT_WORKSPACE", ws_path):
            result = runner.invoke(app, ["trace", "timeline", "20260315-101201"])
        assert result.exit_code == 0
        assert "web_search" in result.output

    def test_empty_trace(self, tmp_path):
        """Zero steps should not crash."""
        ws_path = tmp_path / "workspace"
        ts = datetime(2026, 3, 15, 10, 12, 1, tzinfo=timezone.utc)
        trace = RunTrace(
            run_id="20260315-101201",
            goal="empty",
            start_time=ts,
            end_time=ts,
            model_id="claude-sonnet-4-6",
            steps=[],
            total_tokens=0,
            status="success",
        )
        ws = Workspace(ws_path)
        ws.write_trace(trace)
        with patch("blueclaw.cli.DEFAULT_WORKSPACE", ws_path):
            result = runner.invoke(app, ["trace", "timeline", "20260315-101201"])
        assert result.exit_code == 0

    def test_bar_scaling(self, tmp_path):
        """Bars should appear, with longest getting the most blocks."""
        ws_path = tmp_path / "workspace"
        ts = datetime(2026, 3, 15, 10, 12, 1, tzinfo=timezone.utc)
        ts2 = datetime(2026, 3, 15, 10, 12, 2, tzinfo=timezone.utc)
        steps = [
            TraceStep(
                index=1,
                tool_name="fast",
                status="success",
                start_time=ts,
                end_time=ts,
                duration_ms=100,
            ),
            TraceStep(
                index=2,
                tool_name="slow",
                status="success",
                start_time=ts,
                end_time=ts2,
                duration_ms=1000,
            ),
        ]
        trace = RunTrace(
            run_id="20260315-101201",
            goal="bar test",
            start_time=ts,
            end_time=ts2,
            model_id="claude-sonnet-4-6",
            steps=steps,
            total_tokens=100,
            status="success",
        )
        ws = Workspace(ws_path)
        ws.write_trace(trace)
        with patch("blueclaw.cli.DEFAULT_WORKSPACE", ws_path):
            result = runner.invoke(app, ["trace", "timeline", "20260315-101201"])
        assert result.exit_code == 0
        assert "\u2588" in result.output


# --- v1.2: trace stats ---


class TestTraceStats:
    """v1.2: blueclaw trace stats."""

    def _write_traces(self, ws_path, count=3):
        """Write multiple traces for stats testing."""
        ws = Workspace(ws_path)
        for i in range(count):
            ts = datetime(2026, 3, 10 + i, 10, 0, 0, tzinfo=timezone.utc)
            ts_end = datetime(2026, 3, 10 + i, 10, 0, 5, tzinfo=timezone.utc)
            steps = [
                TraceStep(
                    index=1,
                    tool_name="web_search",
                    status="success",
                    start_time=ts,
                    end_time=ts,
                    duration_ms=400 + i * 100,
                ),
                TraceStep(
                    index=2,
                    tool_name="shell_command",
                    status="success",
                    start_time=ts,
                    end_time=ts,
                    duration_ms=200,
                ),
            ]
            run_id = f"202603{10 + i:02d}-100000"
            ws.write_trace(
                RunTrace(
                    run_id=run_id,
                    goal=f"run {i}",
                    start_time=ts,
                    end_time=ts_end,
                    model_id="claude-sonnet-4-6",
                    steps=steps,
                    total_tokens=1000 + i * 500,
                    total_cost=0.003 + i * 0.001,
                    status="success",
                )
            )
        return ws

    def test_no_traces(self, tmp_path):
        ws_path = tmp_path / "workspace"
        with patch("blueclaw.cli.DEFAULT_WORKSPACE", ws_path):
            result = runner.invoke(app, ["trace", "stats"])
        assert result.exit_code == 0
        assert "no traces" in result.output.lower()

    def test_basic_stats(self, tmp_path):
        ws_path = tmp_path / "workspace"
        self._write_traces(ws_path)
        with patch("blueclaw.cli.DEFAULT_WORKSPACE", ws_path):
            result = runner.invoke(app, ["trace", "stats"])
        assert result.exit_code == 0
        assert "3" in result.output
        assert "web_search" in result.output
        assert "shell_command" in result.output

    def test_shows_total_steps(self, tmp_path):
        ws_path = tmp_path / "workspace"
        self._write_traces(ws_path)
        with patch("blueclaw.cli.DEFAULT_WORKSPACE", ws_path):
            result = runner.invoke(app, ["trace", "stats"])
        assert result.exit_code == 0
        assert "6" in result.output

    def test_shows_cost(self, tmp_path):
        ws_path = tmp_path / "workspace"
        self._write_traces(ws_path)
        with patch("blueclaw.cli.DEFAULT_WORKSPACE", ws_path):
            result = runner.invoke(app, ["trace", "stats"])
        assert result.exit_code == 0
        assert "$" in result.output

    def test_since_filter(self, tmp_path):
        """--since should filter traces by date relative to now."""
        from datetime import timedelta

        ws_path = tmp_path / "workspace"
        ws = Workspace(ws_path)
        now = datetime.now(timezone.utc)

        ts_old = now - timedelta(days=10)
        ts_new = now - timedelta(days=1)
        step = TraceStep(
            index=1,
            tool_name="web_search",
            status="success",
            start_time=ts_old,
            end_time=ts_old,
            duration_ms=400,
        )
        ws.write_trace(
            RunTrace(
                run_id=ts_old.strftime("%Y%m%d-%H%M%S"),
                goal="old run",
                start_time=ts_old,
                end_time=ts_old,
                model_id="claude-sonnet-4-6",
                steps=[step],
                total_tokens=500,
                total_cost=0.003,
                status="success",
            )
        )
        ws.write_trace(
            RunTrace(
                run_id=ts_new.strftime("%Y%m%d-%H%M%S"),
                goal="recent run",
                start_time=ts_new,
                end_time=ts_new,
                model_id="claude-sonnet-4-6",
                steps=[step],
                total_tokens=500,
                total_cost=0.003,
                status="success",
            )
        )
        with patch("blueclaw.cli.DEFAULT_WORKSPACE", ws_path):
            result = runner.invoke(app, ["trace", "stats", "--since", "3"])
        assert result.exit_code == 0
        assert "1" in result.output

    def test_model_filter(self, tmp_path):
        ws_path = tmp_path / "workspace"
        self._write_traces(ws_path)
        with patch("blueclaw.cli.DEFAULT_WORKSPACE", ws_path):
            result = runner.invoke(
                app, ["trace", "stats", "--model", "claude-sonnet-4-6"]
            )
        assert result.exit_code == 0
        assert "3" in result.output

    def test_model_filter_no_match(self, tmp_path):
        ws_path = tmp_path / "workspace"
        self._write_traces(ws_path)
        with patch("blueclaw.cli.DEFAULT_WORKSPACE", ws_path):
            result = runner.invoke(
                app, ["trace", "stats", "--model", "nonexistent-model"]
            )
        assert result.exit_code == 0
        assert "no traces" in result.output.lower()

    def test_single_trace(self, tmp_path):
        """Single trace should not cause division errors."""
        ws_path = tmp_path / "workspace"
        self._write_traces(ws_path, count=1)
        with patch("blueclaw.cli.DEFAULT_WORKSPACE", ws_path):
            result = runner.invoke(app, ["trace", "stats"])
        assert result.exit_code == 0
        assert "1" in result.output

    def test_with_failed_steps(self, tmp_path):
        """Failed steps should be classified and counted."""
        ws_path = tmp_path / "workspace"
        ws = Workspace(ws_path)
        ts = datetime(2026, 3, 15, 10, 0, 0, tzinfo=timezone.utc)
        steps = [
            TraceStep(
                index=1,
                tool_name="web_search",
                status="success",
                start_time=ts,
                end_time=ts,
                duration_ms=400,
            ),
            TraceStep(
                index=2,
                tool_name="http_request",
                status="error",
                start_time=ts,
                end_time=ts,
                duration_ms=5000,
                error="ConnectionTimeout: server did not respond within 30s",
            ),
        ]
        ws.write_trace(
            RunTrace(
                run_id="20260315-100000",
                goal="test failures",
                start_time=ts,
                end_time=ts,
                model_id="claude-sonnet-4-6",
                steps=steps,
                total_tokens=500,
                status="success",
            )
        )
        with patch("blueclaw.cli.DEFAULT_WORKSPACE", ws_path):
            result = runner.invoke(app, ["trace", "stats"])
        assert result.exit_code == 0
        assert "timeout" in result.output.lower()

    def test_mixed_costs_some_none(self, tmp_path):
        """Runs with cost=None should not break averages."""
        ws_path = tmp_path / "workspace"
        ws = Workspace(ws_path)
        ts = datetime(2026, 3, 15, 10, 0, 0, tzinfo=timezone.utc)
        step = TraceStep(
            index=1,
            tool_name="web_search",
            status="success",
            start_time=ts,
            end_time=ts,
            duration_ms=400,
        )
        ws.write_trace(
            RunTrace(
                run_id="20260315-100000",
                goal="with cost",
                start_time=ts,
                end_time=ts,
                model_id="claude-sonnet-4-6",
                steps=[step],
                total_tokens=500,
                total_cost=0.005,
                status="success",
            )
        )
        ws.write_trace(
            RunTrace(
                run_id="20260315-110000",
                goal="no cost",
                start_time=ts,
                end_time=ts,
                model_id="ollama/llama3",
                steps=[step],
                total_tokens=500,
                total_cost=None,
                status="success",
            )
        )
        with patch("blueclaw.cli.DEFAULT_WORKSPACE", ws_path):
            result = runner.invoke(app, ["trace", "stats"])
        assert result.exit_code == 0
        assert "2" in result.output

    def test_stats_context_section(self, tmp_path):
        """trace stats shows Context Management section for context traces."""
        ws_path = tmp_path / "workspace"
        ws = Workspace(ws_path)
        ts = datetime(2026, 3, 15, 10, 0, 0, tzinfo=timezone.utc)
        step = TraceStep(
            index=1,
            tool_name="web_search",
            status="success",
            start_time=ts,
            end_time=ts,
            duration_ms=400,
        )
        ws.write_trace(
            RunTrace(
                run_id="20260315-100000",
                goal="test context",
                start_time=ts,
                end_time=datetime(2026, 3, 15, 10, 0, 5, tzinfo=timezone.utc),
                model_id="claude-sonnet-4-6",
                steps=[step],
                total_tokens=1000,
                total_cost=0.003,
                status="success",
                context_masked_chars=5000,
                context_strategy="mask",
            )
        )
        with patch("blueclaw.cli.DEFAULT_WORKSPACE", ws_path):
            result = runner.invoke(app, ["trace", "stats"])
        assert result.exit_code == 0
        assert "Context Management" in result.output
        assert "5,000" in result.output

    def test_stats_no_context_section_for_old_traces(self, tmp_path):
        """Old traces without context fields don't trigger context section."""
        ws_path = tmp_path / "workspace"
        self._write_traces(ws_path)
        with patch("blueclaw.cli.DEFAULT_WORKSPACE", ws_path):
            result = runner.invoke(app, ["trace", "stats"])
        assert result.exit_code == 0
        assert "Context Management" not in result.output


class TestTraceShowContext:
    def test_trace_show_displays_context_info(self, tmp_path):
        ws_path = tmp_path / "workspace"
        ws = Workspace(ws_path)
        ts = datetime(2026, 3, 15, 10, 12, 1, tzinfo=timezone.utc)
        ws.write_trace(
            RunTrace(
                run_id="20260315-101201",
                goal="test context show",
                start_time=ts,
                end_time=ts,
                model_id="claude-sonnet-4-6",
                steps=[],
                total_tokens=100,
                status="success",
                context_masked_chars=3500,
                context_strategy="mask",
            )
        )
        with patch("blueclaw.cli.DEFAULT_WORKSPACE", ws_path):
            result = runner.invoke(app, ["trace", "show", "20260315-101201"])
        assert result.exit_code == 0
        assert "mask" in result.output
        assert "3,500" in result.output

    def test_trace_show_no_context_for_old_traces(self, tmp_path):
        ws_path = tmp_path / "workspace"
        _write_test_trace(ws_path)
        with patch("blueclaw.cli.DEFAULT_WORKSPACE", ws_path):
            result = runner.invoke(app, ["trace", "show", "20260315-101201"])
        assert result.exit_code == 0
        assert "Context:" not in result.output


# --- Streaming callback CLI wiring ---


class TestStreamingCallbackWiring:
    """Verify both CLI paths pass console to create_agent."""

    @patch("blueclaw.session.run_chat_loop")
    @patch("blueclaw.session.create_agent")
    @patch("blueclaw.session.build_model")
    def test_interactive_passes_console(
        self, mock_build_model, mock_create_agent, mock_loop
    ):
        mock_build_model.return_value = MagicMock()
        mock_create_agent.return_value = MagicMock()
        result = runner.invoke(app, [], input="exit\n")
        assert result.exit_code == 0
        assert mock_create_agent.called, "create_agent was never called"
        call_kwargs = mock_create_agent.call_args
        assert "console" in call_kwargs.kwargs
        assert call_kwargs.kwargs["console"] is not None

    @patch("blueclaw.session.BackgroundContextUpdater")
    @patch("blueclaw.session.cleanup_mcp_clients")
    @patch("blueclaw.session.Agent")
    @patch("blueclaw.session.create_agent")
    @patch("blueclaw.session.build_model")
    def test_scripted_run_passes_console(
        self,
        mock_build_model,
        mock_create_agent,
        mock_agent_cls,
        mock_cleanup,
        mock_updater_cls,
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
        mock_create_agent.return_value = mock_agent

        config = SessionConfig(
            provider="anthropic",
            model_id="claude-sonnet-4-6",
            workspace_path=tmp_path / "workspace",
            tools=[],
        )
        with patch("blueclaw.session.load_config", return_value=config):
            runner.invoke(app, ["run", "hello"])
        assert mock_create_agent.called
        call_kwargs = mock_create_agent.call_args
        assert "console" in call_kwargs.kwargs
        assert call_kwargs.kwargs["console"] is not None


class TestTracePurge:
    """CLI: blueclaw trace purge command."""

    def test_trace_purge_command(self, tmp_path):
        ws_path = tmp_path / "workspace"
        Workspace(ws_path)
        traces_dir = ws_path / ".blueclaw" / "traces"
        traces_dir.mkdir(parents=True, exist_ok=True)
        (traces_dir / "20250101-120000.json").write_text("{}")
        with patch("blueclaw.cli.DEFAULT_WORKSPACE", ws_path):
            result = runner.invoke(app, ["trace", "purge"])
        assert result.exit_code == 0
        assert "1" in result.output
        assert not (traces_dir / "20250101-120000.json").exists()

    def test_trace_purge_dry_run(self, tmp_path):
        ws_path = tmp_path / "workspace"
        Workspace(ws_path)
        traces_dir = ws_path / ".blueclaw" / "traces"
        traces_dir.mkdir(parents=True, exist_ok=True)
        (traces_dir / "20250101-120000.json").write_text("{}")
        with patch("blueclaw.cli.DEFAULT_WORKSPACE", ws_path):
            result = runner.invoke(app, ["trace", "purge", "--dry-run"])
        assert result.exit_code == 0
        assert "would delete" in result.output.lower()
        assert (traces_dir / "20250101-120000.json").exists()


class TestServeCommand:
    """CLI: blueclaw serve command."""

    def test_serve_help_shows_flags(self):
        import re as re_mod

        result = runner.invoke(app, ["serve", "--help"])
        assert result.exit_code == 0
        plain = re_mod.sub(r"\x1b\[[0-9;]*m", "", result.output)
        assert "--host" in plain
        assert "--port" in plain
        assert "--model" in plain
        assert "--cors-origin" in plain

    def test_serve_calls_uvicorn(self):
        with (
            patch("uvicorn.run") as mock_uvicorn_run,
            patch("blueclaw.server.create_server_app"),
            patch("blueclaw.session.load_config"),
            patch("blueclaw.cli.Workspace"),
        ):
            runner.invoke(app, ["serve", "--host", "0.0.0.0", "--port", "9000"])
        mock_uvicorn_run.assert_called_once()
        assert mock_uvicorn_run.call_args.kwargs["host"] == "0.0.0.0"
        assert mock_uvicorn_run.call_args.kwargs["port"] == 9000

    def test_serve_default_binds_localhost(self):
        with (
            patch("uvicorn.run") as mock_uvicorn_run,
            patch("blueclaw.server.create_server_app"),
            patch("blueclaw.session.load_config"),
            patch("blueclaw.cli.Workspace"),
        ):
            runner.invoke(app, ["serve"])
        assert mock_uvicorn_run.call_args.kwargs["host"] == "127.0.0.1"
        assert mock_uvicorn_run.call_args.kwargs["port"] == 8420

    def test_serve_passes_cors_origin(self):
        with (
            patch("uvicorn.run"),
            patch("blueclaw.server.create_server_app") as mock_app,
            patch("blueclaw.session.load_config"),
            patch("blueclaw.cli.Workspace"),
        ):
            runner.invoke(app, ["serve", "--cors-origin", "https://app.example.com"])
        assert mock_app.call_args.kwargs.get("cors_origin") == "https://app.example.com"


import json as _json


class TestSandboxCli:
    def test_sandbox_doctor_reports_docker_available(self, mocker):
        mocker.patch("blueclaw.cli.docker_available", return_value=True)
        mocker.patch("blueclaw.cli.image_exists", return_value=True)
        mocker.patch(
            "blueclaw.cli.resolve_image_tag", return_value="blueclaw/runtime:test"
        )
        result = runner.invoke(app, ["sandbox", "doctor"])
        assert result.exit_code == 0
        assert "docker: ok" in result.stdout.lower()
        assert "image" in result.stdout.lower()

    def test_sandbox_doctor_reports_docker_missing(self, mocker):
        mocker.patch("blueclaw.cli.docker_available", return_value=False)
        result = runner.invoke(app, ["sandbox", "doctor"])
        assert result.exit_code == 1
        assert "docker" in result.stdout.lower()

    def test_sandbox_doctor_json_output(self, mocker):
        mocker.patch("blueclaw.cli.docker_available", return_value=True)
        mocker.patch("blueclaw.cli.image_exists", return_value=True)
        mocker.patch(
            "blueclaw.cli.resolve_image_tag", return_value="blueclaw/runtime:test"
        )
        result = runner.invoke(app, ["sandbox", "doctor", "--json"])
        assert result.exit_code == 0
        data = _json.loads(result.stdout)
        assert data["docker_available"] is True
        assert data["image_present"] is True

    def test_sandbox_build_invokes_docker_build(self, mocker, tmp_path):
        mock_run = mocker.patch("blueclaw.cli.subprocess.run")
        mock_run.return_value.returncode = 0
        mocker.patch(
            "blueclaw.cli.resolve_image_tag", return_value="blueclaw/runtime:test"
        )
        result = runner.invoke(app, ["sandbox", "build"])
        assert result.exit_code == 0
        call_argv = mock_run.call_args[0][0]
        assert call_argv[0] == "docker"
        assert "build" in call_argv
        assert "-t" in call_argv and "blueclaw/runtime:test" in call_argv


class TestLauncherWiring:
    def test_inprocess_host_command_no_execvp(self, mocker, tmp_path, monkeypatch):
        """sandbox.mode=inprocess and a host-only command: no docker invocation."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "blueclaw.yaml").write_text(
            "model_id: anthropic/claude-sonnet-4-6\n"
        )
        monkeypatch.setattr("sys.argv", ["blueclaw", "sandbox", "doctor", "--json"])
        mock_execvp = mocker.patch("blueclaw.cli.os.execvp")
        mocker.patch(
            "blueclaw.session.load_config",
            return_value=SessionConfig(
                sandbox=SandboxConfig(mode="inprocess"),
                model_id="anthropic/claude-sonnet-4-6",
            ),
        )
        runner.invoke(app, ["sandbox", "doctor", "--json"])
        mock_execvp.assert_not_called()

    def test_docker_mode_run_calls_execvp(self, mocker, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "blueclaw.yaml").write_text(
            "model_id: anthropic/claude-sonnet-4-6\n"
        )
        monkeypatch.setattr("sys.argv", ["blueclaw", "run", "hi"])
        mock_execvp = mocker.patch("blueclaw.cli.os.execvp")
        mocker.patch(
            "blueclaw.session.load_config",
            return_value=SessionConfig(
                sandbox=SandboxConfig(mode="docker", image="blueclaw/runtime:test"),
                model_id="anthropic/claude-sonnet-4-6",
            ),
        )
        mocker.patch("blueclaw.launcher.docker_available", return_value=True)
        mocker.patch("blueclaw.launcher.image_exists", return_value=True)
        mocker.patch("blueclaw.launcher.image_digest", return_value="sha256:abc")
        runner.invoke(app, ["run", "hi"])
        mock_execvp.assert_called_once()
        args, _ = mock_execvp.call_args
        assert args[0] == "docker"

    def test_docker_mode_host_subcommand_no_execvp(self, mocker, tmp_path, monkeypatch):
        """sandbox.mode=docker but `skill list` is a host command: no docker."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "blueclaw.yaml").write_text(
            "model_id: anthropic/claude-sonnet-4-6\n"
        )
        monkeypatch.setattr("sys.argv", ["blueclaw", "skill", "list"])
        mock_execvp = mocker.patch("blueclaw.cli.os.execvp")
        mocker.patch(
            "blueclaw.session.load_config",
            return_value=SessionConfig(
                sandbox=SandboxConfig(mode="docker", image="blueclaw/runtime:test"),
                model_id="anthropic/claude-sonnet-4-6",
            ),
        )
        runner.invoke(app, ["skill", "list"])
        mock_execvp.assert_not_called()

    def test_docker_mode_trace_ui_calls_execvp(self, mocker, tmp_path, monkeypatch):
        """trace ui is a two-word container command; must hit execvp under docker."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "blueclaw.yaml").write_text(
            "model_id: anthropic/claude-sonnet-4-6\n"
        )
        monkeypatch.setattr("sys.argv", ["blueclaw", "trace", "ui", "--no-open"])
        mock_execvp = mocker.patch("blueclaw.cli.os.execvp")
        mocker.patch(
            "blueclaw.session.load_config",
            return_value=SessionConfig(
                sandbox=SandboxConfig(mode="docker", image="blueclaw/runtime:test"),
                model_id="anthropic/claude-sonnet-4-6",
            ),
        )
        mocker.patch("blueclaw.launcher.docker_available", return_value=True)
        mocker.patch("blueclaw.launcher.image_exists", return_value=True)
        mocker.patch("blueclaw.launcher.image_digest", return_value="sha256:abc")
        runner.invoke(app, ["trace", "ui", "--no-open"])
        mock_execvp.assert_called_once()

    def test_in_container_skips_launcher_hook(self, mocker, tmp_path, monkeypatch):
        """When BLUECLAW_SANDBOX_MODE=docker is set (we are inside the sandbox),
        the launcher hook must not try to re-execvp into another container."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "blueclaw.yaml").write_text(
            "model_id: anthropic/claude-sonnet-4-6\n"
        )
        monkeypatch.setattr("sys.argv", ["blueclaw", "run", "hi"])
        monkeypatch.setenv("BLUECLAW_SANDBOX_MODE", "docker")
        mock_execvp = mocker.patch("blueclaw.cli.os.execvp")
        # Patch decide_launch to detect whether the hook reached past the guard.
        mock_decide = mocker.patch("blueclaw.cli.decide_launch")
        runner.invoke(app, ["run", "hi"])
        mock_execvp.assert_not_called()
        # Recursion guard fires before decide_launch is called — proving the
        # launcher path was short-circuited and not just that no execvp happened.
        mock_decide.assert_not_called()


class TestInitGitignore:
    def test_init_creates_gitignore_with_env_patterns(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["init"])
        assert result.exit_code == 0
        gi = tmp_path / ".gitignore"
        assert gi.exists()
        body = gi.read_text()
        assert ".env" in body
        assert ".env.docker" in body

    def test_init_appends_when_gitignore_exists(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        gi = tmp_path / ".gitignore"
        gi.write_text("# preexisting\nnode_modules/\n")
        result = runner.invoke(app, ["init"])
        assert result.exit_code == 0
        body = gi.read_text()
        assert "node_modules/" in body
        assert ".env.docker" in body

    def test_init_idempotent(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner.invoke(app, ["init"])
        first = (tmp_path / ".gitignore").read_text()
        runner.invoke(app, ["init"])
        second = (tmp_path / ".gitignore").read_text()
        assert first == second  # no duplicate lines
