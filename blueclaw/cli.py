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

# 22×18 color grid: BL=slate blue body, DB=dark outline, YL=gold accents, _=transparent
_BL = "#4A7FAF"
_DB = "#2D4F73"
_YL = "#D4A843"
___ = None

# fmt: off
PIXEL_GRID: list[list[str | None]] = [
    # Row 0-1: ear tips (gold inner)
    [___, ___, ___, _DB, ___, ___, ___, ___, ___, ___, ___, ___, ___, ___, ___, ___, ___, _DB, ___, ___, ___, ___],
    [___, ___, _DB, _YL, _DB, ___, ___, ___, ___, ___, ___, ___, ___, ___, ___, ___, _DB, _YL, _DB, ___, ___, ___],
    # Row 2-3: ears widen (2px gold inner) + head top
    [___, _DB, _BL, _YL, _YL, _DB, ___, ___, ___, ___, ___, ___, ___, ___, ___, _DB, _YL, _YL, _BL, _DB, ___, ___],
    [___, _DB, _BL, _BL, _BL, _BL, _DB, _DB, _DB, _DB, _DB, _DB, _DB, _DB, _DB, _BL, _BL, _BL, _BL, _DB, ___, ___],
    # Row 4-5: forehead + eyes (closer: cols 8 & 12)
    [___, _DB, _BL, _BL, _BL, _BL, _BL, _BL, _BL, _BL, _BL, _BL, _BL, _BL, _BL, _BL, _BL, _BL, _BL, _DB, ___, ___],
    [___, _DB, _BL, _BL, _BL, _BL, _BL, _BL, _DB, _BL, _BL, _BL, _DB, _BL, _BL, _BL, _BL, _BL, _BL, _DB, ___, ___],
    # Row 6-7: nose (col 10) + gold cheeks (2px each) + mouth
    [___, _DB, _BL, _BL, _BL, _BL, _BL, _BL, _BL, _BL, _DB, _BL, _BL, _BL, _BL, _BL, _BL, _BL, _BL, _DB, ___, ___],
    [___, _DB, _BL, _BL, _YL, _YL, _BL, _BL, _BL, _DB, _BL, _DB, _BL, _BL, _BL, _YL, _YL, _BL, _BL, _DB, ___, ___],
    # Row 8-9: chin + neck
    [___, ___, _DB, _BL, _BL, _BL, _BL, _BL, _BL, _BL, _BL, _BL, _BL, _BL, _BL, _BL, _BL, _BL, _DB, ___, ___, ___],
    [___, ___, ___, _DB, _DB, _DB, _DB, _DB, _DB, _DB, _DB, _DB, _DB, _DB, _DB, _DB, _DB, _DB, ___, ___, ___, ___],
    # Row 10-11: upper body
    [___, ___, ___, _DB, _BL, _BL, _BL, _BL, _BL, _BL, _BL, _BL, _BL, _BL, _BL, _BL, _DB, ___, ___, ___, ___, ___],
    [___, ___, _DB, _BL, _BL, _BL, _BL, _BL, _BL, _BL, _BL, _BL, _BL, _BL, _BL, _BL, _BL, _DB, ___, ___, ___, ___],
    # Row 12-13: claw arm extending LEFT + body
    [___, ___, _DB, _DB, _DB, _DB, _DB, _BL, _BL, _BL, _BL, _BL, _BL, _BL, _BL, _BL, _BL, _DB, ___, ___, ___, ___],
    [___, ___, _DB, _YL, _YL, _YL, _DB, _BL, _BL, _BL, _BL, _BL, _BL, _BL, _BL, _BL, _BL, _DB, ___, ___, ___, ___],
    # Row 14-15: claw prongs LEFT + body + tail
    [___, _YL, ___, ___, _YL, ___, ___, _DB, _BL, _BL, _BL, _BL, _BL, _BL, _BL, _BL, _DB, ___, ___, ___, ___, ___],
    [___, ___, ___, ___, ___, ___, ___, _DB, _BL, _BL, _BL, _BL, _BL, _BL, _BL, _BL, _DB, ___, _DB, _DB, ___, ___],
    # Row 16-17: gold paws + tail tip
    [___, ___, ___, ___, ___, ___, _DB, _YL, _YL, _DB, _BL, _BL, _DB, _YL, _YL, _DB, ___, _DB, _BL, _BL, _DB, ___],
    [___, ___, ___, ___, ___, ___, ___, _DB, _DB, ___, ___, ___, ___, _DB, _DB, ___, ___, ___, _DB, _DB, ___, ___],
]
# fmt: on


def render_pixel_art() -> Text:
    """Render the mascot as half-block pixel art (9 output lines from 18 pixel rows)."""
    from rich.style import Style

    text = Text()
    for row_idx in range(0, 18, 2):
        top_row = PIXEL_GRID[row_idx]
        bot_row = PIXEL_GRID[row_idx + 1] if row_idx + 1 < 18 else [None] * 22
        for col in range(22):
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
        if row_idx < 16:
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
    observer = ObserverHooks(console=console)
    model = build_model(config)

    render_welcome_banner(config, workspace, console)

    agent = create_agent(config, workspace, observer, model=model)
    run_chat_loop(agent, workspace, observer, console, config)


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
        build_model,
        cleanup_mcp_clients,
        create_agent,
        load_config,
        print_run_summary,
        update_context_on_exit,
    )

    config_path = Path("blueclaw.yaml")
    config = load_config(config_path, model_override=model)
    workspace = Workspace(config.workspace_path)
    observer = ObserverHooks(console=console)
    model_instance = build_model(config)

    console.print(f"blueclaw run \u00b7 {config.model_id}", style="dim")

    agent = create_agent(
        config, workspace, observer, model=model_instance, scripted=True
    )
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
    finally:
        update_context_on_exit(agent, workspace)
        cleanup_mcp_clients(observer)


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
            "tools:\n  - web\n  - github\n  - pdf\n\nallowlist_domains: []\n"
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
