"""Unit tests for the trace ↔ capture link feature."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from blueclaw.models import RunTrace


def _make_trace(**overrides) -> RunTrace:
    """Minimal RunTrace for round-trip tests."""
    base = dict(
        run_id="20260518-090000-abcd",
        goal="hi",
        start_time=datetime.now(timezone.utc),
        end_time=datetime.now(timezone.utc),
        model_id="anthropic/claude-opus-4-7",
        steps=[],
        total_tokens=0,
        status="success",
    )
    base.update(overrides)
    return RunTrace(**base)


class TestRunTraceCapturePath:
    def test_default_is_none(self):
        t = _make_trace()
        assert t.capture_path is None

    def test_round_trip_with_capture_path(self):
        t = _make_trace(capture_path=".blueclaw/turns/my-chat/turn-005")
        restored = RunTrace.from_json(t.to_json())
        assert restored.capture_path == ".blueclaw/turns/my-chat/turn-005"

    def test_round_trip_with_none(self):
        t = _make_trace(capture_path=None)
        restored = RunTrace.from_json(t.to_json())
        assert restored.capture_path is None


class TestRunnerRelativization:
    """finalize / finalize_error set trace.capture_path when both kwargs given."""

    def _stub_outcome(self, tmp_path, capture_path, workspace_root):
        """Drive runner.finalize with the minimum scaffolding it needs."""
        from datetime import datetime, timezone

        from blueclaw.models import SessionConfig
        from blueclaw.runner import RunnerCtx, finalize
        from blueclaw.observer import ObserverHooks
        from rich.console import Console
        import io

        observer = ObserverHooks(console=Console(file=io.StringIO()), quiet=True)
        observer.mcp_clients = []
        observer.conversation_manager = None

        class _StubAgent:
            messages = [{"role": "assistant", "content": [{"text": "ok"}]}]
            state = type("S", (), {"set": lambda self, *a, **kw: None})()

        ctx = RunnerCtx(observer=observer, agent=_StubAgent())
        result = type(
            "R",
            (),
            {
                "message": {"role": "assistant", "content": [{"text": "ok"}]},
                "metrics": type("M", (), {"accumulated_usage": {}})(),
                "stop_reason": "end_turn",
            },
        )()
        start = datetime.now(timezone.utc)
        end = datetime.now(timezone.utc)
        return finalize(
            ctx,
            result,
            goal="hi",
            source="terminal",
            conversation_id="cid",
            start_time=start,
            end_time=end,
            config=SessionConfig(),
            capture_path=capture_path,
            workspace_root=workspace_root,
        )

    def test_both_kwargs_set_relativizes(self, tmp_path):
        cp = tmp_path / ".blueclaw" / "turns" / "cid" / "turn-001"
        outcome = self._stub_outcome(tmp_path, cp, tmp_path)
        assert outcome.trace.capture_path == ".blueclaw/turns/cid/turn-001"

    def test_workspace_root_missing_leaves_none(self, tmp_path):
        cp = tmp_path / ".blueclaw" / "turns" / "cid" / "turn-001"
        outcome = self._stub_outcome(tmp_path, cp, workspace_root=None)
        assert outcome.trace.capture_path is None

    def test_capture_path_missing_leaves_none(self, tmp_path):
        outcome = self._stub_outcome(
            tmp_path, capture_path=None, workspace_root=tmp_path
        )
        assert outcome.trace.capture_path is None

    def test_capture_path_outside_workspace_raises(self, tmp_path):
        # capture_path not under workspace_root → ValueError propagates
        outside = tmp_path.parent / "elsewhere" / "turn-001"
        with pytest.raises(ValueError):
            self._stub_outcome(tmp_path, outside, workspace_root=tmp_path)


class TestAdapterRelativization:
    """Each adapter writes a trace whose capture_path is workspace-relative."""

    def test_terminal_trace_has_relative_capture_path(self, tmp_path, monkeypatch):
        import io

        from rich.console import Console

        from blueclaw import session as session_mod
        from blueclaw.models import SessionConfig, RunTrace
        from blueclaw.workspace import Workspace
        from tests.helpers.runner_stubs import install_stub_runner

        workspace = Workspace(tmp_path)
        config = SessionConfig()
        install_stub_runner(monkeypatch)

        prompts = iter(["hi"])

        class _StubPromptSession:
            def prompt(self, _label):
                try:
                    return next(prompts)
                except StopIteration:
                    raise EOFError

        monkeypatch.setattr(session_mod, "PromptSession", _StubPromptSession)
        console = Console(file=io.StringIO())
        session_mod.run_chat_loop(workspace, console, config, model=None, scripted=True)

        traces = list(workspace.traces_dir.glob("*.json"))
        assert len(traces) == 1
        trace = RunTrace.from_json(traces[0].read_text())
        assert trace.capture_path is not None
        assert trace.capture_path.startswith(".blueclaw/turns/")
        assert "/turn-001" in trace.capture_path

    def test_http_trace_has_relative_capture_path(self, tmp_path, monkeypatch):
        from starlette.testclient import TestClient

        from blueclaw import server as server_mod
        from blueclaw.models import SessionConfig, RunTrace
        from blueclaw.workspace import Workspace
        from tests.helpers.runner_stubs import install_stub_runner

        workspace = Workspace(tmp_path)
        config = SessionConfig()
        install_stub_runner(monkeypatch)
        app = server_mod.create_server_app(
            config=config, workspace=workspace, model=object()
        )
        client = TestClient(app)
        headers = {}
        if getattr(config, "api_token", None):
            headers["Authorization"] = f"Bearer {config.api_token}"
        resp = client.post(
            "/message",
            json={"message": "hi", "conversation_id": "test-cid"},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text

        traces = list(workspace.traces_dir.glob("*.json"))
        assert len(traces) == 1
        trace = RunTrace.from_json(traces[0].read_text())
        assert trace.capture_path == ".blueclaw/turns/test-cid/turn-001"

    def test_telegram_trace_has_relative_capture_path(self, tmp_path, monkeypatch):
        import asyncio

        from blueclaw.bridges.core import Allowlist, BridgeRouter
        from blueclaw.models import SessionConfig, RunTrace
        from tests.helpers.runner_stubs import install_stub_runner

        install_stub_runner(monkeypatch)
        config = SessionConfig()
        router = BridgeRouter(
            config=config,
            model=object(),
            allowlist=Allowlist(chat_ids=[12345]),
            chats_root=tmp_path,
        )
        asyncio.run(router.handle_message(chat_id=12345, user_id=999, text="hi"))

        chat_ws_root = tmp_path / "12345"
        traces = list((chat_ws_root / ".blueclaw" / "traces").glob("*.json"))
        assert len(traces) == 1
        trace = RunTrace.from_json(traces[0].read_text())
        assert trace.capture_path == ".blueclaw/turns/12345/turn-001"


class TestComputeCapturePreview:
    """The /api/traces handler renders preview/pruned state per row."""

    def _import_helper(self):
        from blueclaw.web import _compute_capture_preview

        return _compute_capture_preview

    def test_missing_dir_is_pruned(self, tmp_path):
        fn = self._import_helper()
        preview, pruned = fn(tmp_path, ".blueclaw/turns/cid/turn-005")
        assert preview is None
        assert pruned is True

    def test_missing_file_is_pruned(self, tmp_path):
        turn = tmp_path / ".blueclaw" / "turns" / "cid" / "turn-005"
        turn.mkdir(parents=True)
        (turn / "messages.json").write_text("[]")
        fn = self._import_helper()
        preview, pruned = fn(tmp_path, ".blueclaw/turns/cid/turn-005")
        assert preview is None
        assert pruned is True

    def test_empty_file_returns_empty_preview(self, tmp_path):
        turn = tmp_path / ".blueclaw" / "turns" / "cid" / "turn-005"
        turn.mkdir(parents=True)
        (turn / "response.txt").write_text("")
        fn = self._import_helper()
        preview, pruned = fn(tmp_path, ".blueclaw/turns/cid/turn-005")
        assert preview == ""
        assert pruned is False

    def test_short_single_line(self, tmp_path):
        turn = tmp_path / ".blueclaw" / "turns" / "cid" / "turn-005"
        turn.mkdir(parents=True)
        (turn / "response.txt").write_text("hello world")
        fn = self._import_helper()
        preview, pruned = fn(tmp_path, ".blueclaw/turns/cid/turn-005")
        assert preview == "hello world"
        assert pruned is False

    def test_long_single_line_truncated(self, tmp_path):
        turn = tmp_path / ".blueclaw" / "turns" / "cid" / "turn-005"
        turn.mkdir(parents=True)
        (turn / "response.txt").write_text("x" * 500)
        fn = self._import_helper()
        preview, pruned = fn(tmp_path, ".blueclaw/turns/cid/turn-005")
        assert preview is not None
        assert preview.endswith("…")
        assert len(preview) == 200  # 199 chars + ellipsis
        assert pruned is False

    def test_multiline_takes_first_line_only(self, tmp_path):
        turn = tmp_path / ".blueclaw" / "turns" / "cid" / "turn-005"
        turn.mkdir(parents=True)
        (turn / "response.txt").write_text("first line\nsecond line\nthird")
        fn = self._import_helper()
        preview, pruned = fn(tmp_path, ".blueclaw/turns/cid/turn-005")
        assert preview == "first line"
        assert pruned is False

    def test_non_utf8_bytes_decoded_with_replacement(self, tmp_path):
        turn = tmp_path / ".blueclaw" / "turns" / "cid" / "turn-005"
        turn.mkdir(parents=True)
        (turn / "response.txt").write_bytes(b"hello \xff world")
        fn = self._import_helper()
        preview, pruned = fn(tmp_path, ".blueclaw/turns/cid/turn-005")
        assert preview is not None
        assert "hello" in preview
        assert pruned is False

    def test_capture_path_none_returns_nothing(self, tmp_path):
        fn = self._import_helper()
        preview, pruned = fn(tmp_path, None)
        assert preview is None
        assert pruned is False  # not pruned — never had one


class TestSerializeTraceSummaryWithCapture:
    """_serialize_trace_summary surfaces preview/pruned correctly."""

    def _make_trace_with_capture(self, capture_path):
        from datetime import datetime, timezone

        from blueclaw.models import RunTrace

        return RunTrace(
            run_id="20260518-090000-abcd",
            goal="hi",
            start_time=datetime.now(timezone.utc),
            end_time=datetime.now(timezone.utc),
            model_id="anthropic/claude-opus-4-7",
            steps=[],
            total_tokens=0,
            status="success",
            capture_path=capture_path,
        )

    def test_with_real_capture_includes_preview(self, tmp_path):
        from blueclaw.web import _serialize_trace_summary

        turn = tmp_path / ".blueclaw" / "turns" / "cid" / "turn-005"
        turn.mkdir(parents=True)
        (turn / "response.txt").write_text("a real response")
        t = self._make_trace_with_capture(".blueclaw/turns/cid/turn-005")
        summary = _serialize_trace_summary(t, workspace_root=tmp_path)
        assert summary["capture_preview"] == "a real response"
        assert "captures_pruned" not in summary

    def test_with_pruned_capture_includes_flag(self, tmp_path):
        from blueclaw.web import _serialize_trace_summary

        t = self._make_trace_with_capture(".blueclaw/turns/cid/turn-005")
        summary = _serialize_trace_summary(t, workspace_root=tmp_path)
        assert summary.get("captures_pruned") is True
        assert "capture_preview" not in summary

    def test_with_no_capture_omits_both(self, tmp_path):
        from blueclaw.web import _serialize_trace_summary

        t = self._make_trace_with_capture(None)
        summary = _serialize_trace_summary(t, workspace_root=tmp_path)
        assert "capture_preview" not in summary
        assert "captures_pruned" not in summary


class TestListTracesEndpoint:
    """GET /api/traces surfaces capture_preview / captures_pruned per row."""

    def _write_trace_and_capture(self, workspace, capture_path_rel, response_text=None):
        from datetime import datetime, timezone

        from blueclaw.models import RunTrace

        run_id = "20260518-090000-abcd"
        t = RunTrace(
            run_id=run_id,
            goal="hi",
            start_time=datetime.now(timezone.utc),
            end_time=datetime.now(timezone.utc),
            model_id="anthropic/claude-opus-4-7",
            steps=[],
            total_tokens=0,
            status="success",
            capture_path=capture_path_rel,
        )
        workspace.write_trace(t)
        if response_text is not None and capture_path_rel is not None:
            full = workspace.root / capture_path_rel
            full.mkdir(parents=True, exist_ok=True)
            (full / "response.txt").write_text(response_text)

    def test_endpoint_surfaces_preview(self, tmp_path):
        from starlette.testclient import TestClient

        from blueclaw.web import create_app
        from blueclaw.workspace import Workspace

        workspace = Workspace(tmp_path)
        self._write_trace_and_capture(
            workspace,
            ".blueclaw/turns/cid/turn-001",
            response_text="hello from agent",
        )
        client = TestClient(create_app(workspace))
        resp = client.get("/api/traces")
        assert resp.status_code == 200
        rows = resp.json()["traces"]
        assert len(rows) == 1
        assert rows[0]["capture_preview"] == "hello from agent"
        assert "captures_pruned" not in rows[0]
        assert rows[0]["capture_path"] == ".blueclaw/turns/cid/turn-001"

    def test_endpoint_surfaces_pruned(self, tmp_path):
        from starlette.testclient import TestClient

        from blueclaw.web import create_app
        from blueclaw.workspace import Workspace

        workspace = Workspace(tmp_path)
        self._write_trace_and_capture(workspace, ".blueclaw/turns/cid/turn-001")
        client = TestClient(create_app(workspace))
        resp = client.get("/api/traces")
        rows = resp.json()["traces"]
        assert rows[0].get("captures_pruned") is True
        assert "capture_preview" not in rows[0]

    def test_endpoint_omits_both_when_no_capture(self, tmp_path):
        from starlette.testclient import TestClient

        from blueclaw.web import create_app
        from blueclaw.workspace import Workspace

        workspace = Workspace(tmp_path)
        self._write_trace_and_capture(workspace, None)
        client = TestClient(create_app(workspace))
        resp = client.get("/api/traces")
        rows = resp.json()["traces"]
        assert "capture_preview" not in rows[0]
        assert "captures_pruned" not in rows[0]
        assert rows[0]["capture_path"] is None
