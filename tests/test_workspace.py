"""Tests for blueclaw.workspace — sandbox enforcement, context/history ops."""

import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from blueclaw.models import RunRecord, RunTrace, TraceStep
from blueclaw.workspace import Workspace, WorkspaceError

# --- Initialization ---


class TestWorkspaceInit:
    def test_workspace_init_creates_dirs(self, tmp_path):
        ws_path = tmp_path / "ws"
        ws = Workspace(ws_path)
        assert ws_path.exists()
        assert (ws_path / ".blueclaw").exists()

    def test_workspace_paths(self, tmp_path):
        ws = Workspace(tmp_path)
        assert ws.root == tmp_path
        assert ws.context_path == tmp_path / "CONTEXT.md"
        assert ws.history_path == tmp_path / ".blueclaw" / "history.jsonl"
        assert ws.last_turn_path == tmp_path / ".blueclaw" / "last_turn.md"

    def test_workspace_resolve(self, tmp_path):
        ws = Workspace(tmp_path)
        resolved = ws.resolve("subdir/file.txt")
        assert resolved == tmp_path / "subdir" / "file.txt"


# --- Path validation (sandbox enforcement) ---


class TestPathValidation:
    def test_validate_path_inside_workspace(self, tmp_path):
        ws = Workspace(tmp_path)
        inner = tmp_path / "subdir" / "file.txt"
        result = ws.validate_path(str(inner))
        assert result == inner

    def test_validate_path_outside_workspace(self, tmp_path):
        ws = Workspace(tmp_path)
        with pytest.raises(WorkspaceError):
            ws.validate_path("/etc/passwd")

    def test_validate_path_traversal_attack(self, tmp_path):
        ws = Workspace(tmp_path)
        with pytest.raises(WorkspaceError):
            ws.validate_path("../../../etc/passwd")

    def test_validate_path_symlink_escape(self, tmp_path):
        ws = Workspace(tmp_path)
        # Create a symlink pointing outside workspace
        link = tmp_path / "evil_link"
        link.symlink_to("/etc")
        with pytest.raises(WorkspaceError):
            ws.validate_path(str(link / "passwd"))

    def test_validate_path_home_dir(self, tmp_path):
        ws = Workspace(tmp_path)
        with pytest.raises(WorkspaceError):
            ws.validate_path(str(Path.home() / "something"))

    def test_validate_path_absolute_outside(self, tmp_path):
        ws = Workspace(tmp_path)
        with pytest.raises(WorkspaceError):
            ws.validate_path("/tmp/evil")

    def test_validate_path_dot_inside(self, tmp_path):
        ws = Workspace(tmp_path)
        result = ws.validate_path("./subdir/file.txt")
        assert result == tmp_path / "subdir" / "file.txt"


# --- Destructive command deny-list ---


class TestDenyList:
    def test_deny_rm_rf(self, tmp_path):
        ws = Workspace(tmp_path)
        with pytest.raises(WorkspaceError):
            ws.validate_command("rm -rf /")

    def test_deny_rm_rf_home(self, tmp_path):
        ws = Workspace(tmp_path)
        with pytest.raises(WorkspaceError):
            ws.validate_command("rm -rf ~")

    def test_deny_rm_r_workspace_root(self, tmp_path):
        ws = Workspace(tmp_path)
        with pytest.raises(WorkspaceError):
            ws.validate_command(f"rm -r {tmp_path}")

    def test_deny_dd(self, tmp_path):
        ws = Workspace(tmp_path)
        with pytest.raises(WorkspaceError):
            ws.validate_command("dd if=/dev/zero of=/dev/sda")

    def test_deny_mkfs(self, tmp_path):
        ws = Workspace(tmp_path)
        with pytest.raises(WorkspaceError):
            ws.validate_command("mkfs.ext4 /dev/sda1")

    def test_deny_shutdown(self, tmp_path):
        ws = Workspace(tmp_path)
        with pytest.raises(WorkspaceError):
            ws.validate_command("shutdown -h now")

    def test_deny_fork_bomb(self, tmp_path):
        ws = Workspace(tmp_path)
        with pytest.raises(WorkspaceError):
            ws.validate_command(":(){ :|:& };:")

    def test_deny_format(self, tmp_path):
        ws = Workspace(tmp_path)
        with pytest.raises(WorkspaceError):
            ws.validate_command("format C:")

    def test_allow_rm_single_file(self, tmp_path):
        ws = Workspace(tmp_path)
        # Should NOT raise
        ws.validate_command(f"rm {tmp_path}/temp.txt")

    def test_allow_rm_subdir(self, tmp_path):
        ws = Workspace(tmp_path)
        # rm -r on a subdirectory (not root) should be allowed
        ws.validate_command(f"rm -r {tmp_path}/subdir")

    def test_deny_patterns_case_insensitive(self, tmp_path):
        ws = Workspace(tmp_path)
        with pytest.raises(WorkspaceError):
            ws.validate_command("RM -RF /")


# --- Context file operations ---


class TestContextOps:
    def test_read_context_exists(self, tmp_workspace):
        ws = Workspace(tmp_workspace)
        content = ws.read_context()
        assert "Test workspace context" in content

    def test_read_context_missing(self, tmp_path):
        ws = Workspace(tmp_path)
        content = ws.read_context()
        assert content == ""

    def test_write_context(self, tmp_path):
        ws = Workspace(tmp_path)
        ws.write_context("# Updated\nNew content.")
        assert ws.context_path.read_text() == "# Updated\nNew content."

    def test_read_history(self, tmp_workspace):
        ws = Workspace(tmp_workspace)
        rec = RunRecord(
            ts=datetime(2026, 3, 14, tzinfo=timezone.utc),
            goal="test",
            tools=["t1"],
            tokens=100,
        )
        ws.history_path.write_text(rec.to_jsonl() + "\n")
        records = ws.read_history()
        assert len(records) == 1
        assert records[0].goal == "test"

    def test_read_history_empty(self, tmp_path):
        ws = Workspace(tmp_path)
        records = ws.read_history()
        assert records == []

    def test_read_history_skips_malformed_lines(self, tmp_workspace):
        ws = Workspace(tmp_workspace)
        rec = RunRecord(
            ts=datetime(2026, 3, 14, tzinfo=timezone.utc),
            goal="good",
            tools=[],
            tokens=0,
        )
        ws.history_path.write_text(
            rec.to_jsonl() + "\n" + "corrupt line\n" + rec.to_jsonl() + "\n"
        )
        records = ws.read_history()
        assert len(records) == 2
        assert all(r.goal == "good" for r in records)

    def test_append_history(self, tmp_workspace):
        ws = Workspace(tmp_workspace)
        rec = RunRecord(
            ts=datetime(2026, 3, 14, tzinfo=timezone.utc),
            goal="appended",
            tools=["t1"],
            tokens=50,
        )
        ws.append_history(rec)
        lines = ws.history_path.read_text().strip().splitlines()
        assert len(lines) == 1
        assert "appended" in lines[0]

    def test_append_history_creates_parent_dirs(self, tmp_path):
        ws_path = tmp_path / "fresh"
        ws = Workspace(ws_path)
        rec = RunRecord(
            ts=datetime(2026, 3, 14, tzinfo=timezone.utc),
            goal="first",
            tools=[],
            tokens=0,
        )
        ws.append_history(rec)
        assert ws.history_path.exists()

    def test_write_read_clear_last_turn_checkpoint(self, tmp_path):
        ws = Workspace(tmp_path)
        ws.write_last_turn_checkpoint("find docs", "assistant output")

        text = ws.read_last_turn_checkpoint()
        assert "find docs" in text
        assert "assistant output" in text

        ws.clear_last_turn_checkpoint()
        assert ws.read_last_turn_checkpoint() == ""


# --- Trace storage ---


def _make_trace(run_id="20260315-101201", goal="test goal"):
    ts = datetime(2026, 3, 15, 10, 12, 1, tzinfo=timezone.utc)
    step = TraceStep(
        index=1,
        tool_name="web_search",
        status="success",
        start_time=ts,
        end_time=ts,
        duration_ms=842,
        input_summary={"query": "test"},
    )
    return RunTrace(
        run_id=run_id,
        goal=goal,
        start_time=ts,
        end_time=ts,
        model_id="claude-sonnet-4-6",
        steps=[step],
        total_tokens=100,
        total_cost=0.001,
        status="success",
    )


class TestTraceStorage:
    def test_traces_dir_property(self, tmp_path):
        ws = Workspace(tmp_path)
        assert ws.traces_dir == tmp_path / ".blueclaw" / "traces"

    def test_write_trace_creates_file(self, tmp_path):
        ws = Workspace(tmp_path)
        trace = _make_trace()
        path = ws.write_trace(trace)
        assert path.exists()
        assert path.name == "20260315-101201.json"

    def test_write_trace_creates_traces_dir(self, tmp_path):
        ws = Workspace(tmp_path)
        trace = _make_trace()
        ws.write_trace(trace)
        assert ws.traces_dir.exists()

    def test_read_trace_roundtrip(self, tmp_path):
        ws = Workspace(tmp_path)
        trace = _make_trace()
        ws.write_trace(trace)
        restored = ws.read_trace("20260315-101201")
        assert restored is not None
        assert restored == trace

    def test_read_trace_not_found(self, tmp_path):
        ws = Workspace(tmp_path)
        assert ws.read_trace("nonexistent") is None

    def test_list_traces_empty(self, tmp_path):
        ws = Workspace(tmp_path)
        assert ws.list_traces() == []

    def test_list_traces_returns_traces(self, tmp_path):
        ws = Workspace(tmp_path)
        ws.write_trace(_make_trace("20260315-100000", "first"))
        ws.write_trace(_make_trace("20260315-110000", "second"))
        ws.write_trace(_make_trace("20260315-120000", "third"))
        traces = ws.list_traces()
        assert len(traces) == 3
        # Newest first (reverse sorted by filename)
        assert traces[0].run_id == "20260315-120000"
        assert traces[2].run_id == "20260315-100000"

    def test_list_traces_respects_limit(self, tmp_path):
        ws = Workspace(tmp_path)
        for i in range(5):
            ws.write_trace(_make_trace(f"20260315-10000{i}", f"goal {i}"))
        traces = ws.list_traces(limit=2)
        assert len(traces) == 2

    def test_list_traces_skips_corrupt_files(self, tmp_path):
        ws = Workspace(tmp_path)
        ws.write_trace(_make_trace("20260315-100000", "good"))
        # Write a corrupt file
        ws.traces_dir.mkdir(parents=True, exist_ok=True)
        (ws.traces_dir / "20260315-110000.json").write_text("not valid json")
        traces = ws.list_traces()
        assert len(traces) == 1
        assert traces[0].goal == "good"
