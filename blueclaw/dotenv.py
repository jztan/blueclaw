"""Minimal KEY=VALUE dotenv parser. No shell expansion, no interpolation."""

from __future__ import annotations

from pathlib import Path


class DotenvParseError(ValueError):
    """Raised when a dotenv file has a malformed line."""


def parse_dotenv(text: str, *, source: str = "<string>") -> dict[str, str]:
    """Parse a KEY=VALUE string into a dict.

    Rules:
        - Blank lines and lines whose first non-whitespace char is '#' are skipped.
        - Each remaining line must be KEY=VALUE.
        - KEY must match [A-Za-z_][A-Za-z0-9_]*.
        - VALUE may be wrapped in matching single or double quotes; quotes are
          stripped and the inner content is taken literally.
        - No $VAR expansion. '#' inside a value is part of the value.
        - Later occurrences of a key override earlier ones.
    """
    result: dict[str, str] = {}
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise DotenvParseError(f"{source}: line {lineno}: missing '=' in {line!r}")
        key, _, value = line.partition("=")
        key = key.strip()
        if not _is_valid_key(key):
            raise DotenvParseError(f"{source}: line {lineno}: invalid key {key!r}")
        value = _strip_quotes(value)
        result[key] = value
    return result


def load_dotenv_files(paths: list[Path]) -> dict[str, str]:
    """Load files in order; later files override earlier keys. Missing files skipped."""
    merged: dict[str, str] = {}
    for path in paths:
        if not path.exists():
            continue
        merged.update(parse_dotenv(path.read_text(), source=str(path)))
    return merged


def _is_valid_key(key: str) -> bool:
    if not key:
        return False
    if not (key[0].isalpha() or key[0] == "_"):
        return False
    return all(c.isalnum() or c == "_" for c in key)


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        return value[1:-1]
    return value
