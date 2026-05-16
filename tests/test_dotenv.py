"""Tests for blueclaw.dotenv — KEY=VALUE parser with no shell expansion."""

import pytest

from blueclaw.dotenv import DotenvParseError, parse_dotenv, load_dotenv_files


class TestParseDotenv:
    def test_basic_kv(self):
        assert parse_dotenv("FOO=bar\n") == {"FOO": "bar"}

    def test_multiple(self):
        text = "FOO=bar\nBAZ=qux\n"
        assert parse_dotenv(text) == {"FOO": "bar", "BAZ": "qux"}

    def test_comments_and_blanks(self):
        text = "# header\nFOO=bar\n\n# trailing\nBAZ=qux\n"
        assert parse_dotenv(text) == {"FOO": "bar", "BAZ": "qux"}

    def test_double_quoted_preserves_spaces(self):
        assert parse_dotenv('FOO="hello world"\n') == {"FOO": "hello world"}

    def test_single_quoted_preserves_equals(self):
        assert parse_dotenv("FOO='a=b=c'\n") == {"FOO": "a=b=c"}

    def test_no_shell_expansion(self):
        # $VAR must remain literal — no expansion.
        assert parse_dotenv("FOO=$HOME\n") == {"FOO": "$HOME"}

    def test_value_with_equals_inside(self):
        assert parse_dotenv("FOO=a=b\n") == {"FOO": "a=b"}

    def test_inline_comment_after_value_kept_literal(self):
        # No magic — `#` only starts a comment at the start of a line.
        assert parse_dotenv("FOO=bar # not a comment\n") == {
            "FOO": "bar # not a comment"
        }

    def test_malformed_no_equals_raises(self):
        with pytest.raises(DotenvParseError) as exc:
            parse_dotenv("FOO\n")
        assert "line 1" in str(exc.value)

    def test_malformed_key_with_space_raises(self):
        with pytest.raises(DotenvParseError) as exc:
            parse_dotenv("FOO BAR=baz\n")
        assert "line 1" in str(exc.value)

    def test_empty_value_allowed(self):
        assert parse_dotenv("FOO=\n") == {"FOO": ""}

    def test_later_key_overrides_earlier(self):
        assert parse_dotenv("FOO=a\nFOO=b\n") == {"FOO": "b"}


class TestLoadDotenvFiles:
    def test_missing_file_returns_empty(self, tmp_path):
        assert load_dotenv_files([tmp_path / "missing.env"]) == {}

    def test_later_file_overrides_earlier(self, tmp_path):
        a = tmp_path / "a.env"
        b = tmp_path / "b.env"
        a.write_text("FOO=fromA\nBAZ=fromA\n")
        b.write_text("FOO=fromB\n")
        assert load_dotenv_files([a, b]) == {"FOO": "fromB", "BAZ": "fromA"}

    def test_parse_error_includes_filename(self, tmp_path):
        f = tmp_path / "bad.env"
        f.write_text("OK=1\nBROKEN\n")
        with pytest.raises(DotenvParseError) as exc:
            load_dotenv_files([f])
        assert "bad.env" in str(exc.value)
        assert "line 2" in str(exc.value)
