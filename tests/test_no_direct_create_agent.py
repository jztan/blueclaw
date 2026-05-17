"""Durability guard.

The runner exists so adapters do not construct agents directly. This test
scans blueclaw/**/*.py for any reference to `create_agent` outside an
allowlist of files that legitimately use it (the runner itself + the module
that defines it, plus adapter files whose migrations are explicitly pending).

When a future adapter migration lands, that file is removed from
ALLOWLIST_PENDING_MIGRATION.

The regex is intentionally noisy — a stray mention of "create_agent" in a
docstring or comment trips it. The fix is always the same: use
runner_session/run_turn, or move the comment.

Tests under tests/ are exempt: test code legitimately constructs agents
directly for stubbing.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent

ALLOWLIST_OWNS_CREATE_AGENT = {
    "blueclaw/session.py",  # defines create_agent
    "blueclaw/runner.py",  # only sanctioned consumer
}

# Adapter files whose migrations are explicitly out of scope for the runner
# branch. Each is removed from this list as its migration lands.
ALLOWLIST_PENDING_MIGRATION = {
    "blueclaw/cli.py",  # terminal migration — Task 9
    "blueclaw/server.py",  # HTTP migration — separate branch
    "blueclaw/bridges/core.py",  # Telegram migration — separate branch
    "blueclaw/testing.py",  # eval migration — same branch; removed in Task 8
}

PATTERN = re.compile(r"\bcreate_agent\b")


def test_no_direct_create_agent_outside_allowlist():
    allowlist = ALLOWLIST_OWNS_CREATE_AGENT | ALLOWLIST_PENDING_MIGRATION
    violators: list[tuple[str, int, str]] = []

    for path in (REPO_ROOT / "blueclaw").rglob("*.py"):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel in allowlist:
            continue
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            if PATTERN.search(line):
                violators.append((rel, lineno, line.strip()))

    assert not violators, (
        "Direct use of create_agent outside the runner. Use "
        "blueclaw.runner.runner_session or run_turn instead. Violators:\n"
        + "\n".join(f"  {p}:{ln}: {src}" for p, ln, src in violators)
    )
