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

from dataclasses import dataclass, field
from typing import Any

from blueclaw.models import RunRecord, RunTrace
from blueclaw.observer import ObserverHooks


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
