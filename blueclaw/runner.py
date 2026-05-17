"""Unified agent runner.

The only sanctioned way to construct, invoke, and tear down a Strands agent
in BlueClaw. Adapters that build agents directly will miss MCP cleanup,
capture wiring, and future cross-surface concerns. Historical bug:
BridgeRouter.handle_message constructed its own agent without MCP cleanup
from the Telegram bridge launch until 2026-05-17, when this module was
introduced.

See docs/superpowers/specs/2026-05-17-unified-agent-runner-design.md
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from blueclaw.models import RunRecord, RunTrace
from blueclaw.observer import ObserverHooks


def _write_capture_artifacts(
    capture_path: Path,
    *,
    response_text: str,
    messages: list,
) -> list[dict]:
    """Write response.txt + messages.json directly into capture_path.

    Returns a list of capture-failure records (empty on success). Each:
        {"stage": "mkdir" | "response.txt" | "messages.json", "error": "<msg>"}

    Best-effort: never raises. Adapters that need to attach case_idx/run_idx
    or other identity wrap the returned entries themselves before recording
    them anywhere durable.
    """
    failures: list[dict] = []

    try:
        capture_path.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        failures.append({"stage": "mkdir", "error": f"{type(e).__name__}: {e}"})
        print(
            f"blueclaw runner: capture failure at {capture_path}: mkdir: {e}",
            file=sys.stderr,
        )
        return failures

    try:
        (capture_path / "response.txt").write_text(response_text)
    except OSError as e:
        failures.append({"stage": "response.txt", "error": f"{type(e).__name__}: {e}"})
        print(
            f"blueclaw runner: capture failure at {capture_path}: response.txt: {e}",
            file=sys.stderr,
        )

    try:
        (capture_path / "messages.json").write_text(
            json.dumps(messages, indent=2, default=str)
        )
    except OSError as e:
        failures.append({"stage": "messages.json", "error": f"{type(e).__name__}: {e}"})
        print(
            f"blueclaw runner: capture failure at {capture_path}: messages.json: {e}",
            file=sys.stderr,
        )

    return failures


@dataclass
class RunnerCtx:
    """Yielded by runner_session. Holds the observer and the live Strands Agent."""

    observer: ObserverHooks
    agent: Any  # strands.Agent — typed loosely to avoid a hard import here


@dataclass
class RunOutcome:
    """Returned by finalize() and run_turn().

    Lifecycle of `agent`:
      - Obtained via runner_session + manual agent(input) + finalize INSIDE
        the `with` block: the agent is fully live.
      - Obtained via run_turn (the convenience wrapper): the agent is ALWAYS
        post-exit when you receive the RunOutcome. agent.messages and
        agent.state remain readable, but invoking the agent or its tools
        will fail because MCP clients have been closed.

    Trace and record are constructed but NOT persisted. Adapters call
    workspace.write_trace(outcome.trace) and workspace.append_history(
    outcome.record) themselves.

    Error handling: outcome.error is None on success. If the agent raised
    (via run_turn's internal catch, or via the adapter's explicit
    finalize_error call), trace and record are None and response_text is "".
    """

    result: Any | None
    agent: Any
    response_text: str
    trace: RunTrace | None
    record: RunRecord | None
    capture_errors: list[dict] = field(default_factory=list)
    error: Exception | None = None
