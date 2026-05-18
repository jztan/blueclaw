"""Durability guard.

Each production-adapter call into the runner (run_chat_loop in session.py,
both endpoints in server.py, BridgeRouter.handle_message in bridges/core.py)
must pass a non-None capture_path. This test scans the three files for any
occurrence of ``capture_path=None`` and fails if found.

Eval (testing.py) is exempt because it constructs its own capture_path
under ~/blueclaw/test-runs/. The runner module itself is exempt because
its function signatures legitimately default to ``capture_path: Path | None
= None``.

The regex is intentionally noisy — a stray mention of ``capture_path=None``
in a docstring, comment, or string literal in any of the three adapter
files will trip this test. That is the same pattern as
tests/test_no_direct_create_agent.py: false positives are cheap to fix
(reword the comment) and catching the real regression is the point.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent

ADAPTER_FILES = (
    "blueclaw/session.py",
    "blueclaw/server.py",
    "blueclaw/bridges/core.py",
)

PATTERN = re.compile(r"capture_path\s*=\s*None")


def test_adapters_pass_non_none_capture_path():
    violators: list[tuple[str, int, str]] = []
    for rel in ADAPTER_FILES:
        path = REPO_ROOT / rel
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            if PATTERN.search(line):
                violators.append((rel, lineno, line.strip()))

    assert not violators, (
        "Adapter passes capture_path=None to the runner. "
        "Compute one via blueclaw.runner.next_capture_path instead. "
        "Violators:\n" + "\n".join(f"  {p}:{ln}: {src}" for p, ln, src in violators)
    )
