"""Unit tests for next_capture_path and validate_session_id."""

from __future__ import annotations

import pytest

from blueclaw.runner import next_capture_path, validate_session_id


class TestValidateSessionId:
    def test_empty_string_rejected(self):
        with pytest.raises(ValueError, match="non-empty"):
            validate_session_id("")

    def test_forward_slash_rejected(self):
        with pytest.raises(ValueError, match="forbidden path char"):
            validate_session_id("a/b")

    def test_backslash_rejected(self):
        with pytest.raises(ValueError, match="forbidden path char"):
            validate_session_id("a\\b")

    def test_null_byte_rejected(self):
        with pytest.raises(ValueError, match="forbidden path char"):
            validate_session_id("a\x00b")

    def test_parent_dir_rejected(self):
        with pytest.raises(ValueError, match="current/parent dir"):
            validate_session_id("..")

    def test_current_dir_rejected(self):
        with pytest.raises(ValueError, match="current/parent dir"):
            validate_session_id(".")

    def test_normal_id_accepted(self):
        validate_session_id("20260518-143022-a1b2")

    def test_client_supplied_id_accepted(self):
        validate_session_id("my-chat-2026")

    def test_numeric_id_accepted(self):
        validate_session_id("487341290")


class TestNextCapturePath:
    def test_empty_dir_returns_turn_001(self, tmp_path):
        result = next_capture_path(tmp_path, "session-a")
        assert result == tmp_path / ".blueclaw" / "turns" / "session-a" / "turn-001"

    def test_creates_parent_dirs(self, tmp_path):
        next_capture_path(tmp_path, "session-a")
        assert (tmp_path / ".blueclaw" / "turns" / "session-a").is_dir()

    def test_with_turn_001_returns_turn_002(self, tmp_path):
        turns_dir = tmp_path / ".blueclaw" / "turns" / "session-a"
        turns_dir.mkdir(parents=True)
        (turns_dir / "turn-001").mkdir()
        result = next_capture_path(tmp_path, "session-a")
        assert result == turns_dir / "turn-002"

    def test_uses_max_not_count(self, tmp_path):
        turns_dir = tmp_path / ".blueclaw" / "turns" / "session-a"
        turns_dir.mkdir(parents=True)
        (turns_dir / "turn-002").mkdir()
        (turns_dir / "turn-005").mkdir()
        result = next_capture_path(tmp_path, "session-a")
        assert result == turns_dir / "turn-006"

    def test_malformed_turn_dirs_ignored(self, tmp_path):
        turns_dir = tmp_path / ".blueclaw" / "turns" / "session-a"
        turns_dir.mkdir(parents=True)
        (turns_dir / "turn-foo").mkdir()
        (turns_dir / "turn-").mkdir()
        (turns_dir / "turn-001").mkdir()
        result = next_capture_path(tmp_path, "session-a")
        assert result == turns_dir / "turn-002"

    def test_non_turn_entries_ignored(self, tmp_path):
        turns_dir = tmp_path / ".blueclaw" / "turns" / "session-a"
        turns_dir.mkdir(parents=True)
        (turns_dir / "scratch").mkdir()
        (turns_dir / "notes.md").write_text("hi")
        result = next_capture_path(tmp_path, "session-a")
        assert result == turns_dir / "turn-001"

    def test_plain_file_named_turn_001_advances_counter(self, tmp_path):
        # A stray plain file named "turn-001" must be counted in numbering —
        # otherwise next_capture_path returns turn-001 and the runner's
        # subsequent mkdir(turn-001) collides with the file. Counting both
        # files and dirs makes the helper collision-safe at the cost of
        # treating any "turn-NNN" entry (regardless of kind) as occupied.
        turns_dir = tmp_path / ".blueclaw" / "turns" / "session-a"
        turns_dir.mkdir(parents=True)
        (turns_dir / "turn-001").write_text("oops")
        result = next_capture_path(tmp_path, "session-a")
        assert result == turns_dir / "turn-002"

    def test_width_extends_past_999(self, tmp_path):
        turns_dir = tmp_path / ".blueclaw" / "turns" / "session-a"
        turns_dir.mkdir(parents=True)
        (turns_dir / "turn-1000").mkdir()
        result = next_capture_path(tmp_path, "session-a")
        assert result == turns_dir / "turn-1001"

    def test_rejected_id_raises_value_error(self, tmp_path):
        with pytest.raises(ValueError):
            next_capture_path(tmp_path, "../etc")

    def test_isolation_per_session(self, tmp_path):
        next_capture_path(tmp_path, "session-a")
        result = next_capture_path(tmp_path, "session-b")
        assert result == tmp_path / ".blueclaw" / "turns" / "session-b" / "turn-001"

    def test_debug_log_on_malformed(self, tmp_path, caplog):
        import logging

        turns_dir = tmp_path / ".blueclaw" / "turns" / "session-a"
        turns_dir.mkdir(parents=True)
        (turns_dir / "turn-foo").mkdir()
        with caplog.at_level(logging.DEBUG, logger="blueclaw.runner"):
            next_capture_path(tmp_path, "session-a")
        assert any("ignoring malformed entry" in r.message for r in caplog.records)
