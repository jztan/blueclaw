"""Durability guard.

Each production-adapter call into the runner (run_chat_loop in session.py,
both endpoints in server.py, BridgeRouter.handle_message in bridges/core.py)
must:
1. Pass a non-None capture_path to the runner.
2. Pass a workspace_root kwarg to finalize/finalize_error (so the runner
   can relativize the capture path for the trace JSON).

Eval (testing.py) is exempt because it constructs its own capture_path
under ~/blueclaw/test-runs/ and does not need workspace-relative paths.
The runner module itself is exempt because its function signatures
legitimately default these to None.

The regex is intentionally noisy — a stray mention of either string in a
docstring, comment, or string literal in any of the three adapter files
will trip these tests. Same pattern as test_no_direct_create_agent.py:
false positives are cheap to fix (reword the comment) and catching the
real regression is the point.
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

CAPTURE_NONE_PATTERN = re.compile(r"capture_path\s*=\s*None")
WORKSPACE_ROOT_PATTERN = re.compile(r"workspace_root\s*=")


def test_adapters_pass_non_none_capture_path():
    violators: list[tuple[str, int, str]] = []
    for rel in ADAPTER_FILES:
        path = REPO_ROOT / rel
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            if CAPTURE_NONE_PATTERN.search(line):
                violators.append((rel, lineno, line.strip()))

    assert not violators, (
        "Adapter passes capture_path=None to the runner. "
        "Compute one via blueclaw.runner.next_capture_path instead. "
        "Violators:\n" + "\n".join(f"  {p}:{ln}: {src}" for p, ln, src in violators)
    )


def test_adapters_pass_workspace_root_to_finalize():
    missing: list[str] = []
    for rel in ADAPTER_FILES:
        path = REPO_ROOT / rel
        text = path.read_text()
        if not WORKSPACE_ROOT_PATTERN.search(text):
            missing.append(rel)

    assert not missing, (
        "Adapter file does not pass workspace_root= to finalize/finalize_error. "
        "Without it, trace.capture_path stays None and the UI cannot link "
        "the trace to its captured artifacts. Missing in:\n"
        + "\n".join(f"  {p}" for p in missing)
    )


def test_bus_for_turn_helper_exists():
    """Phase 1 contract: adapters reach EventBus only via bus_for_turn."""
    from blueclaw.runner import bus_for_turn

    assert callable(bus_for_turn)


def test_adapters_do_not_construct_eventbus_directly():
    """Adapters must use bus_for_turn — no direct EventBus(...) calls.

    Static check via source grep. The bus lifecycle is the runner's job;
    direct construction in an adapter bypasses the observer attachment
    contract and leaks the file handle on the unhappy path.
    """
    import ast
    import pathlib

    adapter_files = [
        pathlib.Path("blueclaw/session.py"),
        pathlib.Path("blueclaw/server.py"),
        pathlib.Path("blueclaw/testing.py"),
        pathlib.Path("blueclaw/bridges/core.py"),
        pathlib.Path("blueclaw/bridges/telegram.py"),
    ]
    for f in adapter_files:
        if not f.exists():
            continue
        tree = ast.parse(f.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id != "EventBus", (
                    f"{f}: direct EventBus(...) call — use "
                    f"bus_for_turn(observer, capture_path) instead"
                )
