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

# --- Pixel art ---

# 24×18 color grid: BL=slate blue body, DB=dark outline, YL=gold accents.
# The stencil is shaped to better match the logo mascot: upright ears, close-set
# face, left claw, round body, and a curled tail on the right.
_BL = "#4A7FAF"
_DB = "#2D4F73"
_YL = "#D4A843"

_PIXEL_ROWS = [
    "........D......D........",
    ".......DYD....DYD.......",
    "......DBYYD..DYYBD......",
    ".....DBBBBBDDBBBBBD.....",
    ".....DBBBBBBBBBBBBBD....",
    "....DBBBDBBBBBDBBBBBD...",
    "....DBBBBBDBDBBBBBBBD...",
    "....DBBYYBBBBBBYYBBBD...",
    "....DBBBBBBDBBBBBBBBD...",
    ".....DBBBBBBBBBBBBBD....",
    "..YYYDDBBBBBBBBBBBDD....",
    ".YD...DYBBBBBBBBBBD.....",
    "Y..YDDDBBBBBBBBBBBBDD...",
    ".YDDBBBBBBBBBBBBBBBBBD..",
    "...DBBBBBBBYYBBBBBBBBDD.",
    "...DBBBBBBBDDBBBBBBBD.BD",
    "....DYYD..D..DYYD..DDBD.",
    ".....DD........DD....DD.",
]

_PIXEL_COLORS = {".": None, "B": _BL, "D": _DB, "Y": _YL}
PIXEL_GRID: list[list[str | None]] = [
    [_PIXEL_COLORS[cell] for cell in row] for row in _PIXEL_ROWS
]


def render_pixel_art() -> Text:
    """Render the mascot as half-block pixel art (9 output lines from 18 pixel rows)."""
    from rich.style import Style

    text = Text()
    row_count = len(PIXEL_GRID)
    col_count = max((len(row) for row in PIXEL_GRID), default=0)
    for row_idx in range(0, row_count, 2):
        top_row = PIXEL_GRID[row_idx]
        bot_row = (
            PIXEL_GRID[row_idx + 1] if row_idx + 1 < row_count else [None] * col_count
        )
        for col in range(col_count):
            fg = top_row[col] if col < len(top_row) else None
            bg = bot_row[col] if col < len(bot_row) else None
            if fg and bg:
                text.append("\u2580", style=Style(color=fg, bgcolor=bg))
            elif fg:
                text.append("\u2580", style=Style(color=fg))
            elif bg:
                text.append("\u2584", style=Style(color=bg))
            else:
                text.append(" ")
        if row_idx + 2 < row_count:
            text.append("\n")
    return text


# --- Welcome banner ---


def render_welcome_banner(
    config: SessionConfig, workspace: Workspace, con: Console
) -> None:
    """Render the welcome banner with mascot, config info, and tips."""
    art = render_pixel_art()

    # Info lines
    info = Text()
    info.append(f"blueclaw v{__version__}\n", style="bold")
    info.append(f"model: {config.model_id}\n")

    history = workspace.read_history()
    run_count = len(history)
    info.append(f"{run_count} past runs\n" if run_count else "No past runs\n")
    info.append(f"{workspace.root}\n", style="dim")

    if workspace.read_last_turn_checkpoint():
        info.append(
            "recovery checkpoint available (.blueclaw/last_turn.md)\n", style="yellow"
        )

    if config.provider == "ollama":
        info.append(
            "running locally \u2014 no data leaves your machine\n", style="dim italic"
        )

    # Tips
    tips = Text()
    tips.append("Tips:\n", style="bold")
    tips.append("  Type a goal to get started\n")
    tips.append("  exit/quit to leave\n")
    tips.append("  CONTEXT.md persists across sessions\n")

    width = con.width or 120
    if width < 60:
        # Narrow: single column
        content = Text()
        content.append_text(art)
        content.append("\n\n")
        content.append_text(info)
        content.append("\n")
        content.append_text(tips)
    else:
        # Wide: two columns via group
        from rich.columns import Columns

        left = Text()
        left.append_text(art)
        left.append("\n\n")
        left.append_text(info)
        content = Columns([left, tips], padding=(0, 2))

    panel = Panel(content, title=f"blueclaw v{__version__}", border_style="blue")
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

        start = _time.time()
        result = agent(prompt)
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
) -> None:
    """Step through a recorded trace interactively."""
    workspace = Workspace(DEFAULT_WORKSPACE)
    trace = workspace.read_trace(run_id)

    if trace is None:
        console.print(f"Trace not found: {run_id}")
        raise typer.Exit(1)

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
    from collections import Counter
    from datetime import datetime, timedelta, timezone

    from blueclaw.models import classify_error

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

    # --- Compute metrics ---
    total_runs = len(traces)
    all_steps = [s for t in traces for s in t.steps]
    total_steps = len(all_steps)
    failed_steps = [s for s in all_steps if s.status == "error"]

    avg_steps = total_steps / total_runs
    avg_tokens = sum(t.total_tokens for t in traces) / total_runs

    costs = [t.total_cost for t in traces if t.total_cost is not None]
    avg_cost = sum(costs) / len(costs) if costs else None
    total_cost = sum(costs) if costs else None

    # Timing
    wall_times = []
    tool_times = []
    for t in traces:
        wall_ms = int((t.end_time - t.start_time).total_seconds() * 1000)
        tool_ms = sum(s.duration_ms for s in t.steps)
        wall_times.append(wall_ms)
        tool_times.append(tool_ms)

    avg_wall = sum(wall_times) / total_runs
    avg_tool = sum(tool_times) / total_runs

    durations_sorted = sorted(wall_times)
    median = durations_sorted[len(durations_sorted) // 2]
    p95 = durations_sorted[int(len(durations_sorted) * 0.95)]

    tool_pct = (avg_tool / avg_wall * 100) if avg_wall > 0 else 0

    # Tool frequency
    tool_counts = Counter(s.tool_name for s in all_steps)

    # --- Display ---
    header = f"Trace Stats \u00b7 {total_runs} runs"
    if since is not None:
        header += f" \u00b7 last {since} days"
    console.print(f"\n[bold]{header}[/bold]\n")

    # Overview
    console.print("[bold]Overview[/bold]")
    console.print(f"  Total runs:     {total_runs}")
    console.print(f"  Total steps:    {total_steps}")
    console.print(f"  Avg steps/run:  {avg_steps:.1f}")
    console.print(f"  Avg tokens/run: {avg_tokens:,.0f}")
    if avg_cost is not None:
        console.print(f"  Avg cost/run:   ${avg_cost:.4f}")
    if total_cost is not None:
        console.print(f"  Total cost:     ${total_cost:.2f}")
    console.print()

    # Timing
    console.print("[bold]Timing[/bold]")
    console.print(f"  Avg duration:    {avg_wall / 1000:.1f}s")
    console.print(f"  Median duration: {median / 1000:.1f}s")
    console.print(f"  p95 duration:    {p95 / 1000:.1f}s")
    console.print(
        f"  Avg tool time:   {avg_tool / 1000:.1f}s ({tool_pct:.0f}% of wall)"
    )
    console.print()

    # Top tools
    console.print("[bold]Top Tools[/bold] (by frequency)")
    for tool_name, count in tool_counts.most_common(10):
        pct = count / total_steps * 100
        console.print(f"  {tool_name:<20} {count} calls ({pct:.0f}%)")
    console.print()

    # Failed steps
    if failed_steps:
        failure_counts = Counter(classify_error(s.error) for s in failed_steps)
        fail_rate = len(failed_steps) / total_steps * 100
        runs_with_failures = len(
            set(t.run_id for t in traces for s in t.steps if s.status == "error")
        )
        console.print(
            f"[bold]Failed Steps[/bold] "
            f"({len(failed_steps)} across {runs_with_failures} runs "
            f"\u00b7 {fail_rate:.1f}% step failure rate)"
        )
        for category, count in failure_counts.most_common():
            pct = count / len(failed_steps) * 100
            console.print(f"  {category:<20} {count} ({pct:.0f}%)")


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
