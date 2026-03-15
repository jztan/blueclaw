"""Tests for blueclaw.session — config, model factory, agent, chat loop."""

from io import StringIO
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import yaml
from rich.console import Console

from blueclaw.models import SessionConfig
from blueclaw.session import (
    _StreamingCallback,
    build_model,
    build_system_prompt,
    cleanup_mcp_clients,
    create_agent,
    extract_text,
    format_trace_for_explanation,
    is_capability_refusal,
    load_config,
    load_tools,
    parse_model_override,
    print_run_summary,
    run_chat_loop,
    update_context_on_exit,
    write_turn_checkpoint,
)
from blueclaw.workspace import Workspace

# --- Config loading ---


class TestLoadConfig:
    def test_load_config_from_yaml(self, tmp_path):
        yaml_path = tmp_path / "blueclaw.yaml"
        yaml_path.write_text(
            yaml.dump(
                {
                    "model": {"provider": "ollama", "model_id": "llama3"},
                    "tools": ["web"],
                }
            )
        )
        cfg = load_config(yaml_path)
        assert cfg.provider == "ollama"
        assert cfg.model_id == "llama3"

    def test_load_config_defaults(self, tmp_path):
        yaml_path = tmp_path / "missing.yaml"
        cfg = load_config(yaml_path)
        assert cfg.provider == "anthropic"
        assert cfg.model_id == "claude-sonnet-4-6"

    def test_load_config_partial_yaml(self, tmp_path):
        yaml_path = tmp_path / "blueclaw.yaml"
        yaml_path.write_text(yaml.dump({"model": {"provider": "litellm"}}))
        cfg = load_config(yaml_path)
        assert cfg.provider == "litellm"
        # Rest should be defaults
        assert cfg.max_tokens == 4096

    def test_load_config_invalid_yaml(self, tmp_path):
        yaml_path = tmp_path / "blueclaw.yaml"
        yaml_path.write_text(": : : invalid yaml [[[")
        with pytest.raises(ValueError):
            load_config(yaml_path)

    def test_load_config_override_model(self, tmp_path):
        yaml_path = tmp_path / "blueclaw.yaml"
        yaml_path.write_text(
            yaml.dump(
                {"model": {"provider": "anthropic", "model_id": "claude-sonnet-4-6"}}
            )
        )
        cfg = load_config(yaml_path, model_override="ollama/llama3")
        assert cfg.provider == "ollama"
        assert cfg.model_id == "llama3"


# --- Model override parsing ---


class TestParseModelOverride:
    def test_parse_model_override_ollama(self):
        provider, model_id = parse_model_override("ollama/llama3")
        assert provider == "ollama"
        assert model_id == "llama3"

    def test_parse_model_override_litellm(self):
        provider, model_id = parse_model_override("litellm/gemini/gemini-2.0-flash")
        assert provider == "litellm"
        assert model_id == "gemini/gemini-2.0-flash"

    def test_parse_model_override_bare(self):
        # Per tech review: bare names should raise ValueError
        with pytest.raises(ValueError, match="provider/model_id"):
            parse_model_override("llama3")


# --- Model factory ---


class TestBuildModel:
    def test_build_model_anthropic(self):
        import strands.models as _sm

        mock_cls = MagicMock()
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}, clear=False):
            with patch.dict(_sm.__dict__, {"AnthropicModel": mock_cls}):
                cfg = SessionConfig(provider="anthropic", model_id="claude-sonnet-4-6")
                build_model(cfg)
                mock_cls.assert_called_once_with(
                    model_id="claude-sonnet-4-6", max_tokens=4096
                )

    def test_build_model_ollama(self):
        with patch.dict("sys.modules", {"ollama": MagicMock()}):
            with patch("strands.models.OllamaModel") as mock_cls:
                cfg = SessionConfig(provider="ollama", model_id="llama3")
                build_model(cfg)
                mock_cls.assert_called_once_with(None, model_id="llama3")

    def test_build_model_litellm(self):
        import strands.models as _sm

        mock_cls = MagicMock()
        with patch.dict(_sm.__dict__, {"LiteLLMModel": mock_cls}):
            cfg = SessionConfig(provider="litellm", model_id="gemini/gemini-2.0-flash")
            build_model(cfg)
            mock_cls.assert_called_once_with(model_id="gemini/gemini-2.0-flash")

    def test_build_model_openai(self):
        import strands.models as _sm

        mock_cls = MagicMock()
        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}, clear=False):
            with patch.dict(_sm.__dict__, {"OpenAIModel": mock_cls}):
                cfg = SessionConfig(provider="openai", model_id="gpt-4.1-mini")
                build_model(cfg)
                mock_cls.assert_called_once_with(model_id="gpt-4.1-mini")

    def test_build_model_anthropic_missing_api_key(self):
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""}, clear=False):
            cfg = SessionConfig(provider="anthropic", model_id="claude-sonnet-4-6")
            with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
                build_model(cfg)

    def test_build_model_openai_missing_api_key(self):
        with patch.dict("os.environ", {"OPENAI_API_KEY": ""}, clear=False):
            cfg = SessionConfig(provider="openai", model_id="gpt-4.1-mini")
            with pytest.raises(ValueError, match="OPENAI_API_KEY"):
                build_model(cfg)

    def test_build_model_unknown_provider(self):
        cfg = SessionConfig(provider="unknown", model_id="test")
        with pytest.raises(ValueError):
            build_model(cfg)


# --- Tool loading ---


class TestLoadTools:
    def test_load_tools_default(self):
        cfg = SessionConfig(tools=["web"], allowlist_domains=["example.com"])
        tools = load_tools(cfg)
        assert len(tools) > 0

    def test_load_tools_empty(self):
        cfg = SessionConfig(tools=[])
        tools = load_tools(cfg)
        assert tools == []


# --- System prompt ---


class TestBuildSystemPrompt:
    def test_system_prompt_includes_context(self, tmp_workspace):
        ws = Workspace(tmp_workspace)
        prompt = build_system_prompt(ws)
        assert "Test workspace context" in prompt

    def test_system_prompt_no_context_file(self, tmp_path):
        ws = Workspace(tmp_path)
        # Remove CONTEXT.md if it exists
        if ws.context_path.exists():
            ws.context_path.unlink()
        prompt = build_system_prompt(ws)
        assert isinstance(prompt, str)

    def test_system_prompt_empty_history(self, tmp_path):
        ws = Workspace(tmp_path)
        prompt = build_system_prompt(ws)
        assert isinstance(prompt, str)

    def test_system_prompt_includes_history_summary(self, tmp_workspace):
        ws = Workspace(tmp_workspace)
        from datetime import datetime, timezone

        from blueclaw.models import RunRecord

        rec = RunRecord(
            ts=datetime(2026, 3, 14, tzinfo=timezone.utc),
            goal="searched docs",
            tools=["web_search"],
            tokens=100,
        )
        ws.append_history(rec)
        prompt = build_system_prompt(ws)
        assert "searched docs" in prompt

    def test_system_prompt_includes_skill_index(self, tmp_path):
        ws = Workspace(tmp_path)
        skills_dir = tmp_path / ".claude" / "skills"
        skills_dir.mkdir(parents=True)
        (skills_dir / "summarize.md").write_text("# Summarize\nSummarize text.")
        prompt = build_system_prompt(ws, skills_dir=skills_dir)
        assert "summarize" in prompt.lower()

    def test_system_prompt_skill_content_not_included(self, tmp_path):
        ws = Workspace(tmp_path)
        skills_dir = tmp_path / ".claude" / "skills"
        skills_dir.mkdir(parents=True)
        (skills_dir / "summarize.md").write_text(
            "# Summarize\nThis is the full skill content that should NOT be in the prompt."
        )
        prompt = build_system_prompt(ws, skills_dir=skills_dir)
        assert "full skill content that should NOT" not in prompt

    def test_system_prompt_includes_current_date(self, tmp_path):
        ws = Workspace(tmp_path)
        prompt = build_system_prompt(ws)
        from datetime import datetime, timezone

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        assert f"Today's date is {today}" in prompt


# --- Agent construction ---


class TestCreateAgent:
    @patch("blueclaw.session.Agent")
    def test_create_agent_called_with_model(self, mock_agent_cls, tmp_path):
        ws = Workspace(tmp_path)
        cfg = SessionConfig()
        model = MagicMock()
        observer = MagicMock()
        create_agent(cfg, ws, observer, model=model)
        call_kwargs = mock_agent_cls.call_args
        assert call_kwargs.kwargs["model"] is model

    @patch("blueclaw.session.Agent")
    def test_create_agent_called_with_tools(self, mock_agent_cls, tmp_path):
        ws = Workspace(tmp_path)
        cfg = SessionConfig(tools=["web"], allowlist_domains=["example.com"])
        observer = MagicMock()
        create_agent(cfg, ws, observer, model=MagicMock())
        call_kwargs = mock_agent_cls.call_args
        assert "tools" in call_kwargs.kwargs
        assert len(call_kwargs.kwargs["tools"]) > 0

    @patch("blueclaw.session.Agent")
    def test_create_agent_called_with_hooks(self, mock_agent_cls, tmp_path):
        ws = Workspace(tmp_path)
        cfg = SessionConfig()
        observer = MagicMock()
        create_agent(cfg, ws, observer, model=MagicMock())
        call_kwargs = mock_agent_cls.call_args
        assert observer in call_kwargs.kwargs["hooks"]

    @patch("blueclaw.session.Agent")
    def test_create_agent_called_with_system_prompt(self, mock_agent_cls, tmp_path):
        ws = Workspace(tmp_path)
        cfg = SessionConfig()
        observer = MagicMock()
        create_agent(cfg, ws, observer, model=MagicMock())
        call_kwargs = mock_agent_cls.call_args
        assert "system_prompt" in call_kwargs.kwargs
        assert isinstance(call_kwargs.kwargs["system_prompt"], str)

    @patch("blueclaw.session.Agent")
    def test_create_agent_uses_streaming_callback(self, mock_agent_cls, tmp_path):
        ws = Workspace(tmp_path)
        cfg = SessionConfig()
        observer = MagicMock()
        create_agent(cfg, ws, observer, model=MagicMock())
        call_kwargs = mock_agent_cls.call_args
        cb = call_kwargs.kwargs["callback_handler"]
        assert isinstance(cb, _StreamingCallback)

    @patch("blueclaw.session.Agent")
    def test_create_agent_threads_console_file(self, mock_agent_cls, tmp_path):
        ws = Workspace(tmp_path)
        cfg = SessionConfig()
        observer = MagicMock()
        buf = StringIO()
        console = Console(file=buf)
        create_agent(cfg, ws, observer, model=MagicMock(), console=console)
        cb = mock_agent_cls.call_args.kwargs["callback_handler"]
        assert cb._file is buf


# --- Chat loop ---


class TestChatLoop:
    @patch("blueclaw.session.write_turn_checkpoint")
    @patch("blueclaw.session.update_context_on_exit")
    @patch("blueclaw.session.PromptSession")
    def test_chat_loop_sends_input_to_agent(
        self, mock_prompt_cls, mock_ctx_update, mock_checkpoint, tmp_path
    ):
        ws = Workspace(tmp_path)
        agent = MagicMock()
        result = MagicMock()
        result.message = "response"
        result.metrics.accumulated_usage = {
            "inputTokens": 10,
            "outputTokens": 5,
            "totalTokens": 15,
        }
        result.stop_reason = "end_turn"
        agent.return_value = result
        observer = MagicMock()
        observer.tools_called = []
        console = Console(file=StringIO())
        cfg = SessionConfig()

        mock_session = MagicMock()
        mock_session.prompt.side_effect = ["hello", "exit"]
        mock_prompt_cls.return_value = mock_session

        run_chat_loop(agent, ws, observer, console, cfg)
        agent.assert_called_once_with("hello")
        mock_checkpoint.assert_called_once()

    @patch("blueclaw.session.update_context_on_exit")
    @patch("blueclaw.session.PromptSession")
    def test_chat_loop_exit_commands(self, mock_prompt_cls, mock_ctx_update, tmp_path):
        ws = Workspace(tmp_path)
        agent = MagicMock()
        observer = MagicMock()
        observer.tools_called = []
        console = Console(file=StringIO())
        cfg = SessionConfig()

        for cmd in ["exit", "quit", "/exit", "/quit"]:
            mock_session = MagicMock()
            mock_session.prompt.side_effect = [cmd]
            mock_prompt_cls.return_value = mock_session
            run_chat_loop(agent, ws, observer, console, cfg)
            agent.assert_not_called()
            agent.reset_mock()

    @patch("blueclaw.session.update_context_on_exit")
    @patch("blueclaw.session.PromptSession")
    def test_chat_loop_empty_input_skipped(
        self, mock_prompt_cls, mock_ctx_update, tmp_path
    ):
        ws = Workspace(tmp_path)
        agent = MagicMock()
        observer = MagicMock()
        observer.tools_called = []
        console = Console(file=StringIO())
        cfg = SessionConfig()

        mock_session = MagicMock()
        mock_session.prompt.side_effect = ["", "   ", "exit"]
        mock_prompt_cls.return_value = mock_session

        run_chat_loop(agent, ws, observer, console, cfg)
        agent.assert_not_called()

    @patch("blueclaw.session.PromptSession")
    def test_chat_loop_keyboard_interrupt(self, mock_prompt_cls, tmp_path):
        ws = Workspace(tmp_path)
        agent = MagicMock()
        observer = MagicMock()
        observer.tools_called = []
        console = Console(file=StringIO())
        cfg = SessionConfig()

        mock_session = MagicMock()
        mock_session.prompt.side_effect = KeyboardInterrupt
        mock_prompt_cls.return_value = mock_session

        # Should not raise
        run_chat_loop(agent, ws, observer, console, cfg)

    @patch("blueclaw.session.PromptSession")
    def test_chat_loop_eof(self, mock_prompt_cls, tmp_path):
        ws = Workspace(tmp_path)
        agent = MagicMock()
        observer = MagicMock()
        observer.tools_called = []
        console = Console(file=StringIO())
        cfg = SessionConfig()

        mock_session = MagicMock()
        mock_session.prompt.side_effect = EOFError
        mock_prompt_cls.return_value = mock_session

        # Should not raise
        run_chat_loop(agent, ws, observer, console, cfg)


# --- Run summary ---


class TestRunSummary:
    def test_run_summary_format(self, tmp_path):
        ws = Workspace(tmp_path)
        observer = MagicMock()
        observer.tools_called = ["web_search", "http_request"]
        console = Console(file=StringIO())
        cfg = SessionConfig()

        result = MagicMock()
        result.metrics.accumulated_usage = {
            "inputTokens": 100,
            "outputTokens": 50,
            "totalTokens": 150,
        }

        print_run_summary(
            result=result,
            goal="test query",
            observer=observer,
            workspace=ws,
            config=cfg,
            console=console,
        )
        output = console.file.getvalue()
        assert "150" in output  # tokens
        assert "2" in output or "steps" in output.lower()

    def test_run_summary_no_cost(self, tmp_path):
        ws = Workspace(tmp_path)
        observer = MagicMock()
        observer.tools_called = []
        console = Console(file=StringIO())
        cfg = SessionConfig(provider="ollama", model_id="llama3")

        result = MagicMock()
        result.metrics.accumulated_usage = {
            "inputTokens": 100,
            "outputTokens": 50,
            "totalTokens": 150,
        }

        print_run_summary(
            result=result,
            goal="test",
            observer=observer,
            workspace=ws,
            config=cfg,
            console=console,
        )
        output = console.file.getvalue()
        assert "$" not in output

    def test_run_summary_records_to_history(self, tmp_path):
        ws = Workspace(tmp_path)
        observer = MagicMock()
        observer.tools_called = ["t1"]
        console = Console(file=StringIO())
        cfg = SessionConfig()

        result = MagicMock()
        result.metrics.accumulated_usage = {
            "inputTokens": 100,
            "outputTokens": 50,
            "totalTokens": 150,
        }

        print_run_summary(
            result=result,
            goal="history test",
            observer=observer,
            workspace=ws,
            config=cfg,
            console=console,
        )
        records = ws.read_history()
        assert len(records) == 1
        assert records[0].goal == "history test"

    def test_run_summary_resets_observer(self, tmp_path):
        ws = Workspace(tmp_path)
        observer = MagicMock()
        observer.tools_called = ["t1"]
        console = Console(file=StringIO())
        cfg = SessionConfig()

        result = MagicMock()
        result.metrics.accumulated_usage = {
            "inputTokens": 10,
            "outputTokens": 5,
            "totalTokens": 15,
        }

        print_run_summary(
            result=result,
            goal="test",
            observer=observer,
            workspace=ws,
            config=cfg,
            console=console,
        )
        observer.reset.assert_called_once()


# --- Context update ---


class TestContextUpdate:
    def test_session_end_writes_context(self, tmp_path):
        ws = Workspace(tmp_path)
        ws.write_last_turn_checkpoint("goal", "checkpoint")
        agent = MagicMock()
        mock_result = MagicMock()
        mock_result.message = "Updated context content."
        agent.return_value = mock_result

        update_context_on_exit(agent, ws)
        assert ws.context_path.read_text() == "Updated context content."
        assert ws.read_last_turn_checkpoint() == ""

    def test_context_update_calls_agent(self, tmp_path):
        ws = Workspace(tmp_path)
        agent = MagicMock()
        mock_result = MagicMock()
        mock_result.message = "summary"
        agent.return_value = mock_result

        update_context_on_exit(agent, ws)
        agent.assert_called_once()
        prompt = agent.call_args[0][0]
        assert "CONTEXT.md" in prompt
        assert "facts" in prompt

    def test_context_update_keeps_existing_when_empty(self, tmp_path):
        ws = Workspace(tmp_path)
        ws.write_context("existing")
        ws.write_last_turn_checkpoint("goal", "checkpoint")
        agent = MagicMock()
        mock_result = MagicMock()
        mock_result.message = ""
        agent.return_value = mock_result

        update_context_on_exit(agent, ws)
        assert ws.context_path.read_text() == "existing"
        assert "checkpoint" in ws.read_last_turn_checkpoint()

    def test_context_update_keeps_existing_on_error(self, tmp_path):
        ws = Workspace(tmp_path)
        ws.write_context("existing")
        ws.write_last_turn_checkpoint("goal", "checkpoint")
        agent = MagicMock(side_effect=RuntimeError("boom"))

        update_context_on_exit(agent, ws)
        assert ws.context_path.read_text() == "existing"
        assert "checkpoint" in ws.read_last_turn_checkpoint()

    def test_context_update_handles_dict_message(self, tmp_path):
        ws = Workspace(tmp_path)
        agent = MagicMock()
        mock_result = MagicMock()
        mock_result.message = {
            "role": "assistant",
            "content": [{"type": "text", "text": "# Updated\n- new fact"}],
        }
        agent.return_value = mock_result

        update_context_on_exit(agent, ws)
        assert "new fact" in ws.context_path.read_text()

    def test_context_update_handles_text_object(self, tmp_path):
        ws = Workspace(tmp_path)
        agent = MagicMock()
        mock_result = MagicMock()
        mock_result.message = {"content": [SimpleNamespace(text="# Obj\n- fact")]}
        agent.return_value = mock_result

        update_context_on_exit(agent, ws)
        assert "fact" in ws.context_path.read_text()


class TestMcpCleanup:
    def test_cleanup_mcp_clients_stops_clients(self):
        observer = MagicMock()
        c1 = MagicMock()
        c2 = MagicMock()
        observer.mcp_clients = [c1, c2]

        cleanup_mcp_clients(observer)

        c1.stop.assert_called_once_with(None, None, None)
        c2.stop.assert_called_once_with(None, None, None)


class TestContextCheckpoint:
    def test_extract_text_from_nested_payload(self):
        payload = {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "hello"},
                {"nested": [{"text": "world"}]},
            ],
        }
        assert "hello" in extract_text(payload)
        assert "world" not in extract_text(payload)

    def test_extract_text_ignores_role_field(self):
        payload = {"role": "assistant", "text": "answer"}
        text = extract_text(payload)
        assert text == "answer"
        assert "assistant" not in text

    def test_is_capability_refusal(self):
        assert is_capability_refusal("I don't have access to tools.")
        assert not is_capability_refusal("## Preferences\n- Likes concise responses")

    def test_write_turn_checkpoint_writes_last_turn_file(self, tmp_path):
        ws = Workspace(tmp_path)
        ws.write_context("# Workspace Context\n\n## Preferences")

        write_turn_checkpoint(ws, "find docs", "assistant answer")

        text = ws.read_last_turn_checkpoint()
        assert "# Last Turn Checkpoint" in text
        assert "find docs" in text
        assert "assistant answer" in text

    def test_write_turn_checkpoint_does_not_modify_context(self, tmp_path):
        ws = Workspace(tmp_path)
        ws.write_context("# Stable Context")

        write_turn_checkpoint(ws, "goal", "answer")

        assert ws.read_context() == "# Stable Context"

    def test_write_turn_checkpoint_strips_system_reminder(self, tmp_path):
        ws = Workspace(tmp_path)
        write_turn_checkpoint(
            ws,
            "goal",
            "assistant output\n<system-reminder>internal note</system-reminder>",
        )

        text = ws.read_last_turn_checkpoint()
        assert "assistant output" in text
        assert "system-reminder" not in text


# --- format_trace_for_explanation ---


class TestFormatTraceForExplanation:
    def test_includes_goal_in_output(self, sample_trace):
        result = format_trace_for_explanation(sample_trace)
        assert "research MCP Python SDKs" in result

    def test_includes_step_tool_names(self, sample_trace):
        result = format_trace_for_explanation(sample_trace)
        assert "web_search" in result
        assert "http_request" in result

    def test_includes_step_inputs(self, sample_trace):
        result = format_trace_for_explanation(sample_trace)
        assert "query" in result
        assert "test search" in result

    def test_includes_step_outputs(self, sample_trace):
        result = format_trace_for_explanation(sample_trace)
        assert "Found 10 results" in result

    def test_includes_step_errors(self, error_trace):
        result = format_trace_for_explanation(error_trace)
        assert "ConnectionTimeout" in result

    def test_includes_timing(self, sample_trace):
        result = format_trace_for_explanation(sample_trace)
        assert "842ms" in result

    def test_includes_model_id(self, sample_trace):
        result = format_trace_for_explanation(sample_trace)
        assert "claude-sonnet-4-6" in result

    def test_empty_steps(self):
        from datetime import datetime, timezone
        from blueclaw.models import RunTrace

        ts = datetime(2026, 3, 15, 10, 0, 0, tzinfo=timezone.utc)
        trace = RunTrace(
            run_id="20260315-100000",
            goal="empty run",
            start_time=ts,
            end_time=ts,
            model_id="claude-sonnet-4-6",
            steps=[],
            total_tokens=100,
            status="success",
        )
        result = format_trace_for_explanation(trace)
        assert "empty run" in result
        assert "claude-sonnet-4-6" in result


# --- Streaming callback ---


import sys


class TestStreamingCallback:
    def test_data_chunk_flushed_immediately(self):
        """Core fix: each chunk is available in the buffer without waiting for complete."""
        buf = StringIO()
        cb = _StreamingCallback(file=buf)
        cb(data="hello")
        assert buf.getvalue() == "hello"
        cb(data=" world")
        assert buf.getvalue() == "hello world"

    def test_complete_adds_single_newline(self):
        buf = StringIO()
        cb = _StreamingCallback(file=buf)
        cb(data="done", complete=True)
        assert buf.getvalue() == "done\n"

    def test_multi_chunk_then_complete(self):
        """Simulates realistic streaming: several data chunks followed by a complete signal."""
        buf = StringIO()
        cb = _StreamingCallback(file=buf)
        cb(data="The answer")
        cb(data=" is 42")
        cb(data=".", complete=True)
        assert buf.getvalue() == "The answer is 42.\n"

    def test_reasoning_then_data(self):
        """Both reasoningText and data can appear; reasoning comes first."""
        buf = StringIO()
        cb = _StreamingCallback(file=buf)
        cb(reasoningText="thinking...")
        cb(data="result", complete=True)
        assert buf.getvalue() == "thinking...result\n"

    def test_tool_use_event_ignored(self):
        buf = StringIO()
        cb = _StreamingCallback(file=buf)
        cb(
            event={
                "contentBlockStart": {
                    "start": {"toolUse": {"name": "test", "toolUseId": "x"}}
                }
            }
        )
        assert buf.getvalue() == ""

    def test_empty_data_no_output(self):
        buf = StringIO()
        cb = _StreamingCallback(file=buf)
        cb(data="", complete=False)
        assert buf.getvalue() == ""

    def test_defaults_to_stdout(self):
        cb = _StreamingCallback()
        assert cb._file is sys.stdout

    @patch("builtins.print")
    def test_flush_called_on_every_write(self, mock_print):
        """Regression guard: removing flush=True would break streaming responsiveness."""
        fake_file = MagicMock()
        cb = _StreamingCallback(file=fake_file)
        cb(data="chunk1")
        cb(data="chunk2", complete=True)
        assert mock_print.call_count == 2
        for call in mock_print.call_args_list:
            assert call.kwargs["flush"] is True
