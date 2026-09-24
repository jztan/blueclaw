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
import logging
import secrets
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Any, Iterator

from rich.console import Console

from blueclaw.cancellation import CancellationControl
from blueclaw.models import RunRecord, RunTrace, SessionConfig
from blueclaw.observer import ObserverHooks
from blueclaw.session import (
    build_trace_and_record,
    cleanup_mcp_clients,
    create_agent,
    extract_text,
)
from blueclaw.workspace import Workspace

logger = logging.getLogger(__name__)


_FORBIDDEN_ID_CHARS = ("/", "\\", "\x00")
_MAX_SESSION_ID_LEN = 128  # filesystem-safe upper bound; tightens HTTP attack surface


def validate_session_id(session_id: str) -> None:
    """Reject session IDs that would break out of the workspace.

    Pure — no I/O, no side effects. HTTP's `<id>` is the client-supplied
    `conversation_id` (free-form string), so this validation is load-bearing
    for that adapter. Terminal and Telegram mint IDs locally; the check is
    defense-in-depth there. Adapters that need early validation (before
    grabbing a conversation lock, before opening a stream) should call this
    directly rather than calling `next_capture_path` for its side effect.
    """
    if not session_id:
        raise ValueError("session_id must be non-empty")
    if len(session_id) > _MAX_SESSION_ID_LEN:
        raise ValueError(
            f"session_id exceeds max length {_MAX_SESSION_ID_LEN}: "
            f"got {len(session_id)} chars"
        )
    if any(ch in session_id for ch in _FORBIDDEN_ID_CHARS):
        raise ValueError(f"session_id contains forbidden path char: {session_id!r}")
    for ch in session_id:
        if ch.isspace() or ord(ch) < 32:
            raise ValueError(
                f"session_id contains whitespace or control char: {session_id!r}"
            )
    if session_id in (".", ".."):
        raise ValueError(f"session_id resolves to current/parent dir: {session_id!r}")


def next_capture_path(workspace_root: Path, session_id: str) -> Path:
    """Return the path for the next turn capture directory.

    Layout: ``<workspace_root>/.blueclaw/conversations/<session_id>/turns/turn-NNN/``.
    Numbering is filesystem-derived: scans existing entries whose names match
    ``turn-NNN`` (any kind — directory OR plain file — to remain collision-safe
    against operator-created stray files) and returns ``max + 1`` (or
    ``turn-001`` if none). Entries whose name starts with ``turn-`` but whose
    suffix is not a parseable integer are ignored (with a debug log line).

    No locking. HTTP serializes per-cid via conv_locks; terminal is
    single-process; Telegram serializes per-chat via ChatContext.lock.

    Side effect: creates the parent ``conversations/<session_id>/turns/``
    directory if it does not already exist. Callers wanting pure validation
    should use ``validate_session_id`` instead.
    """
    validate_session_id(session_id)
    turns_dir = workspace_root / ".blueclaw" / "conversations" / session_id / "turns"
    turns_dir.mkdir(parents=True, exist_ok=True)

    existing: list[int] = []
    for p in turns_dir.iterdir():
        if not p.name.startswith("turn-"):
            continue
        suffix = p.name[len("turn-") :]
        if not suffix.isdigit():
            logger.debug("turn-capture: ignoring malformed entry %s", p)
            continue
        existing.append(int(suffix))

    next_n = max(existing, default=0) + 1
    return turns_dir / f"turn-{next_n:03d}"


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
    cancellation: CancellationControl | None = None
    cleanup_errors: list[dict] = field(default_factory=list)
    _closed: bool = False

    def close(self) -> list[dict]:
        """Close MCP clients once and retain every cleanup failure."""
        if not self._closed:
            self._closed = True
            self.cleanup_errors = cleanup_mcp_clients(self.observer) or []
        return self.cleanup_errors


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
    cleanup_errors: list[dict] = field(default_factory=list)


def _mint_run_id(start_time: datetime) -> str:
    return start_time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)


def _status_after_cleanup(
    status: str, reason: str | None, cleanup_errors: list[dict]
) -> tuple[str, str | None, str | None]:
    if cleanup_errors:
        return "error", "cleanup_failed", reason
    return status, reason, None


def _observed_usage(agent) -> dict:
    """Return only reported SDK usage; missing metrics are not estimated."""
    metrics = getattr(agent, "event_loop_metrics", None)
    usage = getattr(metrics, "accumulated_usage", None)
    return usage if isinstance(usage, dict) else {}


def _partial_trace_and_record(
    *,
    observer,
    agent,
    goal: str,
    source: str,
    conversation_id: str | None,
    start_time: datetime,
    end_time: datetime,
    config: SessionConfig,
    run_id: str,
    status: str,
    reason: str | None,
    original_reason: str | None,
    usage_complete: bool,
) -> tuple[RunTrace, RunRecord]:
    usage = _observed_usage(agent)
    tokens = usage.get("totalTokens", 0)
    trace = RunTrace(
        run_id=run_id,
        goal=goal,
        start_time=start_time,
        end_time=end_time,
        model_id=config.model_id,
        steps=list(observer.trace_steps),
        total_tokens=tokens,
        total_cost=None,
        status=status,
        termination_reason=reason,
        original_termination_reason=original_reason,
        usage_complete=usage_complete,
        source=source,
        conversation_id=conversation_id,
    )
    record = RunRecord(
        ts=end_time,
        goal=goal,
        tools=list(observer.tools_called),
        tokens=tokens,
        cost=None,
        conversation_id=conversation_id,
        status=status,
        termination_reason=reason,
        original_termination_reason=original_reason,
        usage_complete=usage_complete,
    )
    return trace, record


def finalize(
    ctx: RunnerCtx,
    result,
    *,
    goal: str,
    source: str,
    conversation_id: str | None,
    start_time: datetime,
    end_time: datetime,
    config: SessionConfig,
    capture_path: Path | None = None,
    workspace_root: Path | None = None,
    run_id: str | None = None,
    status: str | None = None,
    termination_reason: str | None = None,
    response_text: str | None = None,
    usage_complete: bool = True,
) -> RunOutcome:
    """Build trace + record from a completed agent run, optionally write capture.

    Does NOT persist trace/history — adapters call workspace.write_trace
    and append_history themselves.
    """
    if run_id is None:
        run_id = _mint_run_id(start_time)

    if response_text is None:
        response_text = (
            extract_text(result.message) if getattr(result, "message", None) else ""
        )
    trace, record = build_trace_and_record(
        result,
        goal,
        ctx.observer,
        config,
        run_id,
        start_time,
        end_time,
        source=source,
        conversation_id=conversation_id,
    )
    if status is None:
        status = (
            "cancelled"
            if getattr(result, "stop_reason", None) == "cancelled"
            else "success"
        )
    if status == "cancelled" and termination_reason is None:
        termination_reason = "user"
    status, reason, original_reason = _status_after_cleanup(
        status, termination_reason, ctx.cleanup_errors
    )
    for item in (trace, record):
        item.status = status
        item.termination_reason = reason
        item.original_termination_reason = original_reason
        item.usage_complete = usage_complete and status == "success"
        if not item.usage_complete:
            if isinstance(item, RunTrace):
                item.total_cost = None
            else:
                item.cost = None

    if capture_path is not None and workspace_root is not None:
        # Pure path arithmetic — ValueError if capture_path is not under
        # workspace_root, which would indicate an adapter bug worth surfacing.
        trace.capture_path = str(capture_path.relative_to(workspace_root))

    capture_errors: list[dict] = []
    if capture_path is not None:
        capture_errors = _write_capture_artifacts(
            capture_path,
            response_text=response_text,
            messages=list(getattr(ctx.agent, "messages", [])),
        )

    return RunOutcome(
        result=result,
        agent=ctx.agent,
        response_text=response_text,
        trace=trace,
        record=record,
        capture_errors=capture_errors,
        error=None,
        cleanup_errors=list(ctx.cleanup_errors),
    )


def finalize_error(
    ctx: RunnerCtx,
    error: Exception,
    *,
    goal: str,
    source: str,
    conversation_id: str | None,
    start_time: datetime,
    end_time: datetime,
    config: SessionConfig,
    capture_path: Path | None = None,
    workspace_root: Path | None = None,
    run_id: str | None = None,
    response_text: str = "",
    status: str = "error",
    termination_reason: str = "exception",
) -> RunOutcome:
    """Build a RunOutcome when the adapter caught the exception itself.

    Used by adapters whose error semantics differ from the runner's default
    (e.g. terminal's "print and continue the loop"). Without this path,
    those adapters silently skip capture on every agent error.

    Capture current-turn text supplied by the adapter, plus available SDK
    messages. Usage remains partial because a final AgentResult is absent.
    """
    if run_id is None:
        run_id = _mint_run_id(start_time)
    status, reason, original_reason = _status_after_cleanup(
        status, termination_reason, ctx.cleanup_errors
    )
    trace, record = _partial_trace_and_record(
        observer=ctx.observer,
        agent=ctx.agent,
        goal=goal,
        source=source,
        conversation_id=conversation_id,
        start_time=start_time,
        end_time=end_time,
        config=config,
        run_id=run_id,
        status=status,
        reason=reason,
        original_reason=original_reason,
        usage_complete=False,
    )

    if capture_path is not None and workspace_root is not None:
        # Symmetry with finalize: validate the relationship even though no
        # trace exists yet to carry the relativized path. Surfaces adapter
        # bugs (capture outside workspace) consistently across success/error.
        trace.capture_path = str(capture_path.relative_to(workspace_root))

    capture_errors: list[dict] = []
    if capture_path is not None:
        capture_errors = _write_capture_artifacts(
            capture_path,
            response_text=response_text,
            messages=list(getattr(ctx.agent, "messages", [])),
        )

    return RunOutcome(
        result=None,
        agent=ctx.agent,
        response_text=response_text,
        trace=trace,
        record=record,
        capture_errors=capture_errors,
        error=error,
        cleanup_errors=list(ctx.cleanup_errors),
    )


def finalize_unstarted(
    *,
    goal: str,
    source: str,
    conversation_id: str | None,
    start_time: datetime,
    end_time: datetime,
    config: SessionConfig,
    capture_path: Path | None,
    workspace_root: Path,
    termination_reason: str,
) -> RunOutcome:
    """Record a request stopped before agent construction or admission."""
    observer = ObserverHooks(console=Console(file=StringIO()), quiet=True)
    run_id = _mint_run_id(start_time)
    trace, record = _partial_trace_and_record(
        observer=observer,
        agent=None,
        goal=goal,
        source=source,
        conversation_id=conversation_id,
        start_time=start_time,
        end_time=end_time,
        config=config,
        run_id=run_id,
        status="cancelled",
        reason=termination_reason,
        original_reason=None,
        usage_complete=True,
    )
    if capture_path is not None:
        trace.capture_path = str(capture_path.relative_to(workspace_root))
    with bus_for_turn(observer, capture_path, cid=conversation_id) as bus:
        errors = (
            _write_capture_artifacts(capture_path, response_text="", messages=[])
            if capture_path is not None
            else []
        )
        if bus is not None:
            bus.emit({"type": "run.terminal", "status": "cancelled", "run_id": run_id})
    return RunOutcome(
        result=None,
        agent=None,
        response_text="",
        trace=trace,
        record=record,
        capture_errors=errors,
    )


def _bus_targets(observer) -> list:
    """Collect every bus-aware component reachable from observer."""
    targets = [observer]
    cm = getattr(observer, "conversation_manager", None)
    if cm is not None and hasattr(cm, "bus"):
        targets.append(cm)
    return targets


@contextmanager
def bus_for_turn(observer, capture_path: Path | None, *, cid: str | None = None):
    """Create an EventBus for a single turn and attach it to every bus-aware
    component reachable from the observer.

    capture_path may be None when an adapter has no capture wiring; in
    that case we yield None and every component's emit guard no-ops. This
    is the single chokepoint for per-turn bus lifecycle — adapters call
    this rather than constructing EventBus directly.

    cid, when provided, is forwarded to EventBus so it can opportunistically
    connect to a running LiveBroker and forward events in real time.

    Bus-aware components in Phase 1: ObserverHooks (event emit for tool /
    model / message hooks), ObservationMaskingManager (event emit for
    context.mask). Both expose a settable `.bus` attribute. The masking
    manager is reachable via `observer.conversation_manager`, which
    session.py sets after construction.
    """
    if capture_path is None or observer is None:
        yield None
        return

    from blueclaw.events import EventBus

    try:
        capture_path.mkdir(parents=True, exist_ok=True)
        bus = EventBus(capture_path / "events.jsonl", cid=cid)
    except OSError:
        # Disk-full or permission error: fall back to no-bus mode so the turn
        # still completes.  Observability is degraded but the agent is not broken.
        yield None
        return

    targets = _bus_targets(observer)
    prev_buses: list[tuple[Any, Any]] = [(t, getattr(t, "bus", None)) for t in targets]
    for t in targets:
        t.bus = bus

    try:
        yield bus
    finally:
        for t, prev in prev_buses:
            t.bus = prev
        bus.close()


_UNSET = object()


@contextmanager
def runner_session(
    config: SessionConfig,
    workspace: Workspace,
    model,
    *,
    session_manager=None,
    channel: str = "terminal",
    callback_handler=_UNSET,
    scripted: bool = True,
    observer_console: Console | None = None,
    observer_quiet: bool = True,
    cancellation: CancellationControl | None = None,
) -> Iterator[RunnerCtx]:
    """The only sanctioned way to construct an agent in BlueClaw.

    Yields a RunnerCtx holding a fresh ObserverHooks and an Agent.
    On exit runs cleanup_mcp_clients unconditionally — no adapter can
    forget this. See module docstring for the historical bug.

    observer_console / observer_quiet let terminal pass its real Rich
    console (quiet=False so tool calls print inline). All other adapters
    use the default StringIO + quiet=True.
    """
    if observer_console is None:
        observer_console = Console(file=StringIO())
    if cancellation is None:
        cancellation = CancellationControl()
    observer = ObserverHooks(
        console=observer_console, quiet=observer_quiet, cancellation=cancellation
    )

    create_agent_kwargs = dict(
        config=config,
        workspace=workspace,
        observer=observer,
        model=model,
        scripted=scripted,
        session_manager=session_manager,
        channel=channel,
        cancellation=cancellation,
    )
    if callback_handler is not _UNSET:
        create_agent_kwargs["callback_handler"] = callback_handler

    agent = create_agent(**create_agent_kwargs)
    cancellation.attach(agent)
    ctx = RunnerCtx(observer=observer, agent=agent, cancellation=cancellation)
    try:
        yield ctx
    finally:
        ctx.close()


def run_turn(
    config: SessionConfig,
    workspace: Workspace,
    model,
    agent_input,
    *,
    goal: str,
    source: str,
    conversation_id: str | None = None,
    session_manager=None,
    channel: str = "terminal",
    callback_handler=_UNSET,
    scripted: bool = True,
    capture_path: Path | None = None,
    workspace_root: Path | None = None,
) -> RunOutcome:
    """Convenience for non-streaming, per-request adapters.

    Enters runner_session, invokes agent(agent_input), calls finalize
    (or finalize_error if the agent raised), exits. The returned RunOutcome
    carries a post-exit agent — agent.messages and agent.state are readable
    but agent invocation will fail.
    """
    with runner_session(
        config,
        workspace,
        model,
        session_manager=session_manager,
        channel=channel,
        callback_handler=callback_handler,
        scripted=scripted,
    ) as ctx:
        with bus_for_turn(ctx.observer, capture_path, cid=conversation_id):
            start_time = datetime.now(timezone.utc)
            try:
                result = ctx.agent(agent_input)
                end_time = datetime.now(timezone.utc)
                return finalize(
                    ctx,
                    result,
                    goal=goal,
                    source=source,
                    conversation_id=conversation_id,
                    start_time=start_time,
                    end_time=end_time,
                    config=config,
                    capture_path=capture_path,
                    workspace_root=workspace_root,
                )
            except Exception as exc:
                end_time = datetime.now(timezone.utc)
                return finalize_error(
                    ctx,
                    exc,
                    goal=goal,
                    source=source,
                    conversation_id=conversation_id,
                    start_time=start_time,
                    end_time=end_time,
                    config=config,
                    capture_path=capture_path,
                    workspace_root=workspace_root,
                )
