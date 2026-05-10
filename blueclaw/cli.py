"""CLI entrypoints — interactive, scripted, init, history."""

from __future__ import annotations

import warnings
from io import StringIO
from pathlib import Path
from typing import Optional

# Suppress external noise (PyMuPDF/SWIG, Pydantic/Strands serialization)
warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", message="Pydantic serializer warnings")

# Load .env before anything reads os.environ
from dotenv import load_dotenv

load_dotenv()

import typer
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from blueclaw import __version__
from blueclaw.models import SessionConfig
from blueclaw.workspace import Workspace

app = typer.Typer(add_completion=False)
console = Console()

DEFAULT_WORKSPACE = Path.home() / "blueclaw" / "workspace"

# --- Terminal mascot ---

_MASCOT_BLUE = "#3F8FC5"
_MASCOT_YELLOW = "#F4C542"
_MASCOT_SEGMENTS = [
    [
        (" ", None),
        ("\\ ", _MASCOT_YELLOW),
        ("•   •", _MASCOT_BLUE),
        (" /", _MASCOT_YELLOW),
    ],
    [
        ("  ", None),
        ("▝▛███▜▘", _MASCOT_BLUE),
    ],
    [
        ("  ", None),
        ("/", _MASCOT_YELLOW),
        ("▘▘ ", _MASCOT_BLUE),
        ("▝▝", _MASCOT_BLUE),
        ("\\", _MASCOT_YELLOW),
    ],
]


def _render_mascot_lines() -> list[Text]:
    """Render mascot rows as individual Rich text lines."""
    lines: list[Text] = []
    for row in _MASCOT_SEGMENTS:
        line = Text()
        for segment, color in row:
            if color:
                line.append(segment, style=color)
            else:
                line.append(segment)
        lines.append(line)
    return lines


def render_pixel_art() -> Text:
    """Render the terminal mascot in Rich text."""
    text = Text()
    lines = _render_mascot_lines()
    for row_idx, line in enumerate(lines):
        text.append_text(line)
        if row_idx + 1 < len(lines):
            text.append("\n")
    return text


# --- Welcome banner ---


def render_welcome_banner(
    config: SessionConfig, workspace: Workspace, con: Console
) -> None:
    """Render the welcome banner with mascot and config info."""
    mascot_lines = _render_mascot_lines()

    history = workspace.read_history()
    run_count = len(history)

    # Third line: run count + optional status flags
    status_parts = [f"{run_count} past runs" if run_count else "No past runs"]
    if workspace.read_last_turn_checkpoint():
        status_parts.append("recovery checkpoint")
    if config.provider == "ollama":
        status_parts.append("local mode")
    status_str = " \u00b7 ".join(status_parts)

    header_lines = [
        Text.assemble(
            mascot_lines[0],
            (f"   v{__version__} \u00b7 {config.model_id}", "bold"),
        ),
        Text.assemble(mascot_lines[1], (f"   {workspace.root}", "dim")),
        Text.assemble(mascot_lines[2], (f"   {status_str}", "dim")),
    ]

    content = Text()
    for i, line in enumerate(header_lines):
        content.append_text(line)
        if i < len(header_lines) - 1:
            content.append("\n")

    panel = Panel(content, title="BlueClaw", border_style="blue")
    con.print(panel)


# --- Commands ---


def run_session(model_override: str | None = None) -> None:
    """Set up and run an interactive session."""
    from blueclaw.observer import ObserverHooks
    from blueclaw.session import (
        build_model,
        create_agent,
        load_config,
        run_chat_loop,
    )

    config_path = Path("blueclaw.yaml")
    config = load_config(config_path, model_override=model_override)
    workspace = Workspace(config.workspace_path)
    workspace.purge_old_traces(config.trace_retention_days)
    observer = ObserverHooks(console=console)
    model = build_model(config)

    render_welcome_banner(config, workspace, console)

    agent = create_agent(config, workspace, observer, model=model, console=console)
    run_chat_loop(agent, workspace, observer, console, config, model=model)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"blueclaw {__version__}")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    model: Optional[str] = typer.Option(
        None, "--model", "-m", help="Model override (provider/model_id)"
    ),
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        callback=_version_callback,
        is_eager=True,
        help="Show version",
    ),
) -> None:
    """blueclaw — terminal automation agent."""
    if ctx.invoked_subcommand is None:
        run_session(model_override=model)


@app.command()
def run(
    prompt: str = typer.Argument(..., help="Prompt to execute"),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Model override"),
) -> None:
    """Run a single prompt and exit."""
    from blueclaw.observer import ObserverHooks
    from blueclaw.session import (
        BackgroundContextUpdater,
        build_model,
        cleanup_mcp_clients,
        create_agent,
        load_config,
        print_run_summary,
    )

    config_path = Path("blueclaw.yaml")
    config = load_config(config_path, model_override=model)
    workspace = Workspace(config.workspace_path)
    workspace.purge_old_traces(config.trace_retention_days)
    observer = ObserverHooks(console=console)
    model_instance = build_model(config)

    console.print(f"blueclaw run \u00b7 {config.model_id}", style="dim")

    agent = create_agent(
        config,
        workspace,
        observer,
        model=model_instance,
        scripted=True,
        console=console,
    )
    updater = BackgroundContextUpdater(model_instance, workspace)
    try:
        import time as _time
        from blueclaw.uploads import (
            UploadError,
            build_agent_input,
            parse_at_attachments,
        )

        cleaned_message, attachments, failed = parse_at_attachments(prompt)
        for att in attachments:
            console.print(
                f"[dim]attached:[/dim] {att.path} ({att.mime_type})"
            )
        for token, reason in failed:
            console.print(
                f"[yellow]could not attach[/yellow] {token}: {reason}"
            )
        try:
            agent_input = build_agent_input(attachments, cleaned_message)
        except UploadError as exc:
            console.print(f"[yellow]could not attach:[/yellow] {exc}")
            raise typer.Exit(1)

        start = _time.time()
        result = agent(agent_input)
        elapsed = _time.time() - start

        # Strands streams the response via callback — don't reprint

        print_run_summary(
            result=result,
            goal=prompt,
            observer=observer,
            workspace=workspace,
            config=config,
            console=console,
            elapsed=elapsed,
            start_time=start,
        )

        updater.trigger(agent)
    finally:
        cleanup_mcp_clients(observer)
        updater.wait()


@app.command()
def init() -> None:
    """Initialize a blueclaw workspace."""
    workspace = Workspace(DEFAULT_WORKSPACE)

    # Create CONTEXT.md with default content if missing
    if not workspace.context_path.exists():
        workspace.write_context(
            "# Workspace Context\n\n## Preferences\n\n## Projects\n"
        )

    # Create config yaml if missing
    config_path = Path("blueclaw.yaml")
    if not config_path.exists():
        config_path.write_text(
            "model:\n  provider: anthropic\n  model_id: claude-sonnet-4-6\n\n"
            "workspace:\n  path: ~/blueclaw/workspace/\n"
            "  trace_retention_days: 30\n\n"
            "tools:\n  - web\n  - shell\n  - pdf\n\nallowlist_domains: []\n"
        )

    console.print(f"Workspace initialized at {workspace.root}")


@app.command()
def history(
    limit: int = typer.Option(20, "--limit", "-n", help="Max entries to show"),
) -> None:
    """View run history."""
    workspace = Workspace(DEFAULT_WORKSPACE)
    records = workspace.read_history()

    if not records:
        console.print("No runs yet.")
        return

    for rec in records[-limit:]:
        cost_str = f" \u00b7 ${rec.cost:.4f}" if rec.cost else ""
        console.print(
            f"[dim]{rec.ts.strftime('%Y-%m-%d %H:%M')}[/dim] "
            f"{rec.goal} "
            f"[dim]({', '.join(rec.tools)}) \u00b7 {rec.tokens} tokens{cost_str}[/dim]"
        )


# --- Trace commands ---

trace_app = typer.Typer(add_completion=False, help="Inspect execution traces.")
app.add_typer(trace_app, name="trace")


@trace_app.command("list")
def trace_list(
    limit: int = typer.Option(20, "--limit", "-n", help="Max traces to show"),
) -> None:
    """List recent execution traces."""
    workspace = Workspace(DEFAULT_WORKSPACE)
    traces = workspace.list_traces(limit=limit)

    if not traces:
        console.print("No traces yet.")
        return

    for t in traces:
        goal = t.goal[:50] + "..." if len(t.goal) > 50 else t.goal
        cost = f"${t.total_cost:.4f}" if t.total_cost is not None else "n/a"
        style = "red" if t.status == "error" else ""
        console.print(
            f"[dim]{t.run_id}[/dim]  {t.status:<7}  "
            f"{len(t.steps)} steps  {t.total_tokens} tokens  "
            f"{cost}  {goal}",
            style=style,
        )


@trace_app.command("show")
def trace_show(
    run_id: str = typer.Argument(..., help="Run ID to display"),
) -> None:
    """Show detailed trace for a run."""
    from rich.table import Table

    workspace = Workspace(DEFAULT_WORKSPACE)
    trace = workspace.read_trace(run_id)

    if trace is None:
        console.print(f"Trace not found: {run_id}")
        raise typer.Exit(1)

    console.print(f"\n[bold]Run:[/bold] {trace.run_id}")
    console.print(f"[bold]Task:[/bold] {trace.goal}")
    console.print(f"[bold]Model:[/bold] {trace.model_id}")
    console.print(f"[bold]Status:[/bold] {trace.status}")
    console.print(
        f"[bold]Time:[/bold] "
        f"{trace.start_time.strftime('%Y-%m-%d %H:%M:%S')} \u2192 "
        f"{trace.end_time.strftime('%H:%M:%S')}"
    )
    if trace.context_strategy:
        console.print(f"[bold]Context:[/bold] {trace.context_strategy}")
    if trace.context_masked_chars:
        console.print(f"[bold]Masked:[/bold] {trace.context_masked_chars:,} chars")
    console.print()

    table = Table(show_edge=False, pad_edge=False)
    table.add_column("#", style="dim", width=4)
    table.add_column("Tool", min_width=15)
    table.add_column("Duration", justify="right", width=10)
    table.add_column("Status", width=8)

    for step in trace.steps:
        dur = f"{step.duration_ms}ms"
        row_style = "red" if step.status == "error" else ""
        table.add_row(
            str(step.index), step.tool_name, dur, step.status, style=row_style
        )

    console.print(table)

    total_dur = sum(s.duration_ms for s in trace.steps)
    cost = f"${trace.total_cost:.4f}" if trace.total_cost is not None else "n/a"
    console.print(
        f"\nTotal: {len(trace.steps)} steps \u00b7 {total_dur}ms "
        f"\u00b7 {trace.total_tokens} tokens \u00b7 {cost}"
    )


@trace_app.command("explain")
def trace_explain(
    run_id: str = typer.Argument(..., help="Run ID to explain"),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Model override"),
) -> None:
    """Explain a recorded trace using an LLM."""
    from blueclaw.session import (
        build_model,
        format_trace_for_explanation,
        load_config,
    )
    from strands import Agent

    workspace = Workspace(DEFAULT_WORKSPACE)
    trace = workspace.read_trace(run_id)

    if trace is None:
        console.print(f"Trace not found: {run_id}")
        raise typer.Exit(1)

    config_path = Path("blueclaw.yaml")
    config = load_config(config_path, model_override=model)
    model_instance = build_model(config)
    formatted = format_trace_for_explanation(trace)

    explain_agent = Agent(
        model=model_instance,
        tools=[],
        system_prompt=(
            "You explain recorded agent traces. Describe what the agent "
            "did step by step and why. This is a post-hoc explanation — "
            "you are interpreting recorded actions, not reporting the "
            "agent's actual reasoning."
        ),
    )
    explain_agent(f"Explain this trace:\n\n{formatted}")

    console.print(
        "\n[dim]Post-hoc explanation \u00b7 not the agent's actual reasoning[/dim]"
    )


@trace_app.command("graph")
def trace_graph(
    run_id: str = typer.Argument(..., help="Run ID to display as tree"),
) -> None:
    """Show execution graph as a tree."""
    from rich.tree import Tree

    workspace = Workspace(DEFAULT_WORKSPACE)
    trace = workspace.read_trace(run_id)

    if trace is None:
        console.print(f"Trace not found: {run_id}")
        raise typer.Exit(1)

    tree = Tree(f"[bold]{trace.goal}[/bold]")
    for step in trace.steps:
        icon = "\u2713" if step.status == "success" else "\u2717"
        input_parts = [f"{k}: {str(v)[:60]}" for k, v in step.input_summary.items()]
        input_str = ", ".join(input_parts) if input_parts else ""
        tree.add(f"{step.tool_name} ({step.duration_ms}ms) {icon}  {input_str}")

    console.print(tree)


@trace_app.command("diff")
def trace_diff(
    id1: str = typer.Argument(..., help="First run ID"),
    id2: str = typer.Argument(..., help="Second run ID"),
) -> None:
    """Compare two execution traces."""
    workspace = Workspace(DEFAULT_WORKSPACE)
    a = workspace.read_trace(id1)
    if a is None:
        console.print(f"Trace not found: {id1}")
        raise typer.Exit(1)
    b = workspace.read_trace(id2)
    if b is None:
        console.print(f"Trace not found: {id2}")
        raise typer.Exit(1)

    def _cost(t):
        return f"${t.total_cost:.4f}" if t.total_cost is not None else "n/a"

    def _dur(t):
        return sum(s.duration_ms for s in t.steps)

    console.print(f"[bold]Run A:[/bold] {a.run_id}  [bold]Run B:[/bold] {b.run_id}")
    console.print(f"[bold]Goal A:[/bold] {a.goal}")
    console.print(f"[bold]Goal B:[/bold] {b.goal}")
    console.print()

    d_steps = len(b.steps) - len(a.steps)
    d_tokens = b.total_tokens - a.total_tokens
    d_dur = _dur(b) - _dur(a)

    def sign(v):
        return f"+{v}" if v > 0 else str(v)

    console.print(f"Steps:  {len(a.steps)} \u2192 {len(b.steps)} ({sign(d_steps)})")
    console.print(
        f"Tokens: {a.total_tokens} \u2192 {b.total_tokens} ({sign(d_tokens)})"
    )
    console.print(f"Cost:   {_cost(a)} \u2192 {_cost(b)}")
    console.print(f"Time:   {_dur(a)}ms \u2192 {_dur(b)}ms ({sign(d_dur)}ms)")


@trace_app.command("replay")
def trace_replay(
    run_id: str = typer.Argument(..., help="Run ID to replay"),
    stub_tools: bool = typer.Option(
        False, "--stub-tools", help="Re-run with recorded outputs"
    ),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Model override"),
) -> None:
    """Step through a recorded trace interactively."""
    workspace = Workspace(DEFAULT_WORKSPACE)
    trace = workspace.read_trace(run_id)

    if trace is None:
        console.print(f"Trace not found: {run_id}")
        raise typer.Exit(1)

    if stub_tools:
        from blueclaw.session import load_config
        from blueclaw.testing import run_stub_replay

        config = load_config(Path("blueclaw.yaml"), model_override=model)
        run_stub_replay(trace, config)
        return

    console.print(f"[bold]Run:[/bold] {trace.run_id}")
    console.print(f"[bold]Goal:[/bold] {trace.goal}")
    console.print(f"[bold]Model:[/bold] {trace.model_id}")
    console.print(f"[bold]Steps:[/bold] {len(trace.steps)}")
    console.print()

    for step in trace.steps:
        icon = "\u2713" if step.status == "success" else "\u2717"
        console.print(
            f"[bold]Step {step.index}:[/bold] {step.tool_name} "
            f"({step.duration_ms}ms) {icon}"
        )
        if step.input_summary:
            for k, v in step.input_summary.items():
                console.print(f"  input {k}: {v}")
        if step.output_summary:
            console.print(f"  output: {step.output_summary}")
        if step.error:
            console.print(f"  [red]error: {step.error}[/red]")

        try:
            resp = input("[Enter] next · [q] quit > ")
        except EOFError:
            break
        if resp.strip().lower() == "q":
            break

    total_dur = sum(s.duration_ms for s in trace.steps)
    cost = f"${trace.total_cost:.4f}" if trace.total_cost is not None else "n/a"
    console.print(
        f"\nTotal: {len(trace.steps)} steps \u00b7 {total_dur}ms "
        f"\u00b7 {trace.total_tokens} tokens \u00b7 {cost}"
    )


@trace_app.command("timeline")
def trace_timeline(
    run_id: str = typer.Argument(..., help="Run ID to display"),
) -> None:
    """Show waterfall timeline of tool calls."""
    from rich.table import Table

    workspace = Workspace(DEFAULT_WORKSPACE)
    trace = workspace.read_trace(run_id)

    if trace is None:
        console.print(f"Trace not found: {run_id}")
        raise typer.Exit(1)

    cost = f"${trace.total_cost:.4f}" if trace.total_cost is not None else "n/a"
    console.print(f"\n[bold]Goal:[/bold] {trace.goal}")
    console.print(
        f"[bold]Model:[/bold] {trace.model_id} \u00b7 "
        f"{len(trace.steps)} steps \u00b7 {trace.total_tokens} tokens \u00b7 {cost}"
    )
    console.print()

    if not trace.steps:
        console.print("No steps recorded.")
        return

    max_dur = max(s.duration_ms for s in trace.steps)
    max_bar = 40

    table = Table(show_edge=False, pad_edge=False)
    table.add_column("#", style="dim", width=4)
    table.add_column("Tool", min_width=15)
    table.add_column("Start", justify="right", width=10)
    table.add_column("Duration", justify="right", width=10)
    table.add_column("Cumulative", justify="right", width=12)
    table.add_column("Bar", min_width=10)

    cumulative = 0
    for step in trace.steps:
        offset_ms = int((step.start_time - trace.start_time).total_seconds() * 1000)
        cumulative += step.duration_ms
        bar_len = int(step.duration_ms / max_dur * max_bar) if max_dur > 0 else 0
        bar = "\u2588" * max(bar_len, 1)

        table.add_row(
            str(step.index),
            step.tool_name,
            f"+{offset_ms}ms",
            f"{step.duration_ms}ms",
            f"{cumulative}ms",
            bar,
        )

    console.print(table)

    wall_ms = int((trace.end_time - trace.start_time).total_seconds() * 1000)
    tool_ms = sum(s.duration_ms for s in trace.steps)
    overhead_ms = wall_ms - tool_ms
    overhead_pct = (overhead_ms / wall_ms * 100) if wall_ms > 0 else 0
    console.print(
        f"\nTool time: {tool_ms}ms \u00b7 Wall time: {wall_ms}ms "
        f"\u00b7 Overhead: {overhead_ms}ms ({overhead_pct:.0f}%)"
    )


@trace_app.command("stats")
def trace_stats(
    since: Optional[int] = typer.Option(
        None, "--since", "-s", help="Only include traces from the last N days"
    ),
    model: Optional[str] = typer.Option(
        None, "--model", "-m", help="Filter by model ID"
    ),
) -> None:
    """Show aggregate trace statistics."""
    from datetime import datetime, timedelta, timezone

    workspace = Workspace(DEFAULT_WORKSPACE)

    since_dt = None
    if since is not None:
        since_dt = datetime.now(timezone.utc) - timedelta(days=since)

    traces = workspace.list_traces(limit=10_000, since=since_dt)

    # Apply model filter
    if model:
        traces = [t for t in traces if t.model_id == model]

    if not traces:
        console.print("No traces yet.")
        return

    from blueclaw.web import compute_stats

    stats = compute_stats(traces)

    # --- Display ---
    header = f"Trace Stats \u00b7 {stats['total_runs']} runs"
    if since is not None:
        header += f" \u00b7 last {since} days"
    console.print(f"\n[bold]{header}[/bold]\n")

    # Overview
    console.print("[bold]Overview[/bold]")
    console.print(f"  Total runs:     {stats['total_runs']}")
    console.print(f"  Total steps:    {stats['total_steps']}")
    console.print(f"  Avg steps/run:  {stats['avg_steps_per_run']}")
    console.print(f"  Avg tokens/run: {stats['avg_tokens_per_run']:,}")
    if stats["avg_cost_per_run"] is not None:
        console.print(f"  Avg cost/run:   ${stats['avg_cost_per_run']:.4f}")
    if stats["total_cost"] is not None:
        console.print(f"  Total cost:     ${stats['total_cost']:.2f}")
    console.print()

    # Timing
    console.print("[bold]Timing[/bold]")
    console.print(f"  Median duration: {stats['median_duration_s']}s")
    console.print(f"  p95 duration:    {stats['p95_duration_s']}s")
    console.print(f"  Avg tool time:   {stats['avg_tool_time_pct']:.0f}% of wall")
    console.print()

    # Context management
    ctx = stats["context"]
    if ctx["runs_with_masking"] > 0:
        console.print("[bold]Context Management[/bold]")
        console.print(
            f"  Runs with masking: " f"{ctx['runs_with_masking']}/{stats['total_runs']}"
        )
        console.print(f"  Avg chars masked:  {ctx['avg_masked_chars']:,}")
        for strat, cnt in ctx["strategies"].items():
            console.print(f"  Strategy {strat}: {cnt} runs")
        console.print()

    # Top tools
    console.print("[bold]Top Tools[/bold] (by frequency)")
    for tool in stats["top_tools"]:
        console.print(f"  {tool['name']:<20} {tool['count']} calls ({tool['pct']}%)")
    console.print()

    # Failed steps
    if stats["failed_steps"] > 0:
        console.print(
            f"[bold]Failed Steps[/bold] "
            f"({stats['failed_steps']} across {stats['runs_with_failures']} "
            f"runs \u00b7 {stats['error_rate']}% step failure rate)"
        )
        for err in stats["errors"]:
            console.print(f"  {err['category']:<20} {err['count']} ({err['pct']}%)")


@trace_app.command("ui")
def trace_ui(
    port: int = typer.Option(8111, "--port", "-p", help="Port to listen on"),
    no_open: bool = typer.Option(
        False, "--no-open", help="Don't open browser automatically"
    ),
) -> None:
    """Launch trace visualization dashboard in browser."""
    import threading
    import webbrowser

    import uvicorn

    from blueclaw.web import create_app

    workspace = Workspace(DEFAULT_WORKSPACE)
    app = create_app(workspace)
    url = f"http://localhost:{port}"
    console.print(f"Trace UI: {url}")

    if not no_open:
        threading.Timer(0.5, webbrowser.open, args=[url]).start()

    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


@trace_app.command("purge")
def trace_purge(
    older_than: Optional[int] = typer.Option(
        None,
        "--older-than",
        "-d",
        help="Delete traces older than N days (default: config or 30)",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be deleted"),
) -> None:
    """Delete old trace files."""
    from blueclaw.session import load_config

    config = load_config(Path("blueclaw.yaml"))
    days = older_than if older_than is not None else config.trace_retention_days
    workspace = Workspace(DEFAULT_WORKSPACE)
    count = workspace.purge_old_traces(days, dry_run=dry_run)
    verb = "Would delete" if dry_run else "Deleted"
    console.print(f"{verb} {count} trace(s) older than {days} days.")


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="Bind host"),
    port: int = typer.Option(8420, help="Bind port"),
    model: Optional[str] = typer.Option(
        None, "--model", help="Model override (provider/model_id)"
    ),
    cors_origin: Optional[str] = typer.Option(
        None, "--cors-origin", help="Additional allowed CORS origin"
    ),
    max_concurrent: Optional[int] = typer.Option(
        None,
        "--max-concurrent",
        help="Cap simultaneous agent runs (overrides server.max_concurrent_runs)",
    ),
) -> None:
    """Start the blueclaw HTTP API server."""
    import uvicorn
    from blueclaw.server import create_server_app
    from blueclaw.session import load_config

    config = load_config(Path("blueclaw.yaml"), model_override=model)
    if max_concurrent is not None:
        config = config.model_copy(update={"max_concurrent_runs": max_concurrent})
    workspace = Workspace(config.workspace_path)
    server_app = create_server_app(config, workspace, cors_origin=cors_origin)
    console.print(f"[bold]blueclaw serve[/bold] listening on http://{host}:{port}")
    uvicorn.run(server_app, host=host, port=port)


@app.command()
def test(
    spec_path: Path = typer.Argument(..., help="Path to test spec YAML"),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Validate spec without running"
    ),
    output: Optional[str] = typer.Option(
        None, "--output", "-o", help="Write results to file"
    ),
    output_format: str = typer.Option(
        "tap", "--format", "-f", help="Output format: tap or junit"
    ),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Model override"),
    keep_workspace: bool = typer.Option(
        False, "--keep-workspace", help="Keep temp workspace for inspection"
    ),
) -> None:
    """Run agent regression tests from a YAML spec."""
    import shutil
    import sys
    import tempfile

    from blueclaw.testing import (
        format_junit,
        format_tap,
        load_spec,
        run_spec,
        validate_spec,
    )
    from blueclaw.session import load_config

    try:
        spec = load_spec(spec_path)
    except Exception as e:
        console.print(f"Error loading spec: {e}")
        raise typer.Exit(1)

    if dry_run:
        issues = validate_spec(spec)
        if issues:
            for w in issues:
                console.print(f"  Warning: {w}")
        else:
            console.print(f"Spec valid: {len(spec.tests)} tests")
        raise typer.Exit(0)

    spec_warnings = validate_spec(spec)
    if spec_warnings:
        progress = Console(stderr=True)
        for w in spec_warnings:
            progress.print(f"  Warning: {w}")

    config = load_config(Path("blueclaw.yaml"), model_override=model or spec.model)
    if spec.allowlist_domains:
        for d in spec.allowlist_domains:
            if d not in config.allowlist_domains:
                config.allowlist_domains.append(d)
    workspace_dir = Path(tempfile.mkdtemp(prefix="blueclaw-test-"))
    try:
        results = run_spec(spec, config, workspace_dir)
        formatted = (
            format_junit(results) if output_format == "junit" else format_tap(results)
        )
        if output:
            Path(output).write_text(formatted)
        else:
            sys.stdout.write(formatted)
    finally:
        if keep_workspace:
            console.print(f"Workspace kept at: {workspace_dir}")
        else:
            shutil.rmtree(workspace_dir, ignore_errors=True)

    raise typer.Exit(1 if any(r.verdict == "fail" for r in results) else 0)
