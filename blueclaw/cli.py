"""CLI entrypoints — interactive, scripted, init, history."""

from __future__ import annotations

import json
import json as _json
import os
import shutil
import subprocess
import sys
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
from blueclaw.launcher import (
    decide_launch,
    docker_available,
    image_exists,
    normalize_subcommand,
    resolve_image_tag,
    should_sandbox_subcommand,
)
from blueclaw.models import SandboxConfig, SessionConfig
from blueclaw.workspace import Workspace

app = typer.Typer(add_completion=False)
console = Console()

DEFAULT_WORKSPACE = Path.home() / "blueclaw" / "workspace"

# --- Skill sub-app ---

skill_app = typer.Typer(help="Manage blueclaw skills.", add_completion=False)
app.add_typer(skill_app, name="skill")


def _default_bind_host() -> str:
    """Bind localhost normally, but 0.0.0.0 inside the sandbox.

    A port published via `docker run --publish 8420:8420` only reaches the
    container if the in-container server is bound to 0.0.0.0 (all interfaces).
    Binding to 127.0.0.1 inside the container makes it unreachable from the
    host, which is the bug the user just hit when http://127.0.0.1:8420/playground
    refused connections.
    """
    if os.environ.get("BLUECLAW_SANDBOX_MODE") == "docker":
        return "0.0.0.0"
    return "127.0.0.1"


def _config_path() -> Path:
    """Location of blueclaw.yaml.

    Honors BLUECLAW_CONFIG when set (used by the docker launcher to point
    the in-container process at the bind-mounted config; mounted outside
    the workspace because macOS VirtioFS won't bind-mount a file inside
    another bind-mount).
    """
    return Path(os.environ.get("BLUECLAW_CONFIG", "blueclaw.yaml"))


def _global_skills_dir() -> Path:
    from blueclaw.skills import default_global_dir

    return default_global_dir()


def _project_skills_dir() -> Path:
    from blueclaw.skills import default_project_dir

    p = default_project_dir()
    if p is None:
        # No blueclaw.yaml found; fall back to cwd-anchored .blueclaw/skills
        p = Path.cwd() / ".blueclaw" / "skills"
    return p


def _resolve_source(source: str, tmp_root: Path) -> Path:
    if source.startswith(("http://", "https://")) and source.lower().rstrip(
        "/"
    ).endswith(("skill.md",)):
        return _fetch_url_skill_md(source, tmp_root)
    if source.startswith(("http://", "https://", "git@", "ssh://", "git://")):
        return _git_clone(source, tmp_root)
    p = Path(source).expanduser().resolve()
    if not p.exists():
        raise typer.BadParameter(f"source path does not exist: {source}")
    return p


def _git_clone(url: str, tmp_root: Path) -> Path:
    """Clone a git URL into tmp_root and return the (sub)directory."""
    import subprocess

    subdir = ""
    if "#" in url:
        url, subdir = url.split("#", 1)
    dest = tmp_root / "clone"
    try:
        res = subprocess.run(
            ["git", "clone", "--depth=1", "--quiet", url, str(dest)],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        raise typer.BadParameter("git clone timed out after 30 seconds")
    if res.returncode != 0:
        raise typer.BadParameter(
            f"git clone failed: {res.stderr.strip() or res.stdout.strip()}"
        )
    skill_path = dest / subdir if subdir else dest
    if not (skill_path / "SKILL.md").exists():
        raise typer.BadParameter(
            f"no SKILL.md at {skill_path} (use #subdir for monorepos)"
        )
    # Skill.from_file validates that the directory name matches the skill name.
    # Rename the skill_path directory to match the name declared in SKILL.md.
    skill_name = _read_skill_name(skill_path / "SKILL.md")
    if skill_name and skill_path.name != skill_name:
        renamed = skill_path.parent / skill_name
        skill_path.rename(renamed)
        skill_path = renamed
    return skill_path


def _fetch_url_skill_md(url: str, tmp_root: Path) -> Path:
    """Fetch a single SKILL.md from an https URL into tmp_root.

    The URL must point directly at raw SKILL.md text. We name the temp
    directory after the skill's frontmatter ``name`` so that
    ``Skill.from_file(strict=True)`` (called by the install pipeline)
    accepts the directory.
    """
    import socket
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            content_bytes = resp.read()
    except (urllib.error.URLError, socket.timeout, ConnectionError) as e:
        raise typer.BadParameter(f"fetch failed: {e}")

    try:
        text = content_bytes.decode("utf-8")
    except UnicodeDecodeError as e:
        raise typer.BadParameter(f"SKILL.md is not valid UTF-8: {e}")

    # Reuse the existing helper. _read_skill_name takes a Path; write the
    # content to a temp file first so we don't duplicate parsing logic.
    scratch = tmp_root / "_scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    scratch_md = scratch / "SKILL.md"
    scratch_md.write_text(text, encoding="utf-8")
    skill_name = _read_skill_name(scratch_md)
    if not skill_name:
        raise typer.BadParameter("could not read skill name from frontmatter")

    skill_dir = tmp_root / skill_name
    skill_dir.mkdir(parents=True, exist_ok=False)
    (skill_dir / "SKILL.md").write_text(text, encoding="utf-8")
    return skill_dir


def _read_skill_name(skill_md: Path) -> str:
    """Extract the 'name' field from a SKILL.md frontmatter block."""
    try:
        text = skill_md.read_text(encoding="utf-8")
    except OSError:
        return ""
    text = text.strip()
    if not text.startswith("---"):
        return ""
    end = text.find("---", 3)
    if end == -1:
        return ""
    for line in text[3:end].splitlines():
        if line.startswith("name:"):
            val = line.split(":", 1)[1].strip()
            # Strip matching outer quotes (valid YAML)
            if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
                val = val[1:-1]
            return val
    return ""


def _confirm_install(skill, target: Path, yes: bool) -> bool:
    summary = (
        f"\nSkill: {skill.name}\n"
        f"  description: {skill.description}\n"
        f"  install to:  {target}\n"
    )
    if skill.license:
        summary += f"  license:     {skill.license}\n"
    if skill.compatibility:
        summary += f"  compat:      {skill.compatibility}\n"
    typer.echo(summary)
    if yes:
        return True
    if not sys.stdin.isatty():
        typer.echo("Refusing to install non-interactively without --yes.", err=True)
        return False
    return typer.confirm("Install?", default=False)


@skill_app.command("install")
def skill_install(
    source: str = typer.Argument(..., help="Local path or git URL"),
    project: bool = typer.Option(False, "--project"),
    force: bool = typer.Option(False, "--force"),
    yes: bool = typer.Option(False, "--yes"),
) -> None:
    """Install a skill from a local directory or git URL."""
    import tempfile

    from strands.vended_plugins.skills import Skill

    target_root = _project_skills_dir() if project else _global_skills_dir()

    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            src_dir = _resolve_source(source, Path(tmpdir))
            skill = Skill.from_file(src_dir, strict=True)
        except (ValueError, FileNotFoundError) as e:
            typer.echo(f"Invalid skill: {e}", err=True)
            raise typer.Exit(code=2)

        target = target_root / skill.name
        if target.exists() and not force:
            typer.echo(
                f"Skill exists at {target} (use --force to overwrite)",
                err=True,
            )
            raise typer.Exit(code=1)

        if not _confirm_install(skill, target, yes):
            typer.echo("Aborted.")
            raise typer.Exit(code=1)

        target_root.mkdir(parents=True, exist_ok=True)
        if target.exists():
            shutil.rmtree(target)
        staging = target.with_name(target.name + ".__staging__")
        if staging.exists():
            shutil.rmtree(staging)
        shutil.copytree(src_dir, staging)
        os.replace(staging, target)
        typer.echo(f"Installed {skill.name} -> {target}")


def _list_installed():
    """Return a list of (Skill, scope, path) tuples for both scopes, project shadowing global."""
    from strands.vended_plugins.skills import Skill

    from blueclaw.skills import resolved_skill_paths

    paths = resolved_skill_paths(
        global_dir=_global_skills_dir(),
        project_dir=_project_skills_dir(),
    )
    out = []
    for p in paths:
        try:
            sk = Skill.from_file(p, strict=False)
        except (ValueError, FileNotFoundError):
            continue
        scope = "project" if p.is_relative_to(_project_skills_dir()) else "global"
        out.append((sk, scope, p))
    return out


@skill_app.command("uninstall")
def skill_uninstall(
    name: str = typer.Argument(...),
    project: bool = typer.Option(False, "--project"),
    yes: bool = typer.Option(False, "--yes"),
) -> None:
    """Remove an installed skill from global (default) or project scope."""
    root = _project_skills_dir() if project else _global_skills_dir()
    target = root / name
    if not target.exists():
        typer.echo(f"Skill not found: {name}", err=True)
        raise typer.Exit(code=1)
    if not yes:
        if not sys.stdin.isatty():
            typer.echo(
                "Refusing to uninstall non-interactively without --yes.",
                err=True,
            )
            raise typer.Exit(code=1)
        if not typer.confirm(f"Remove {target}?", default=False):
            typer.echo("Aborted.")
            raise typer.Exit(code=1)
    shutil.rmtree(target)
    typer.echo(f"Removed {name}")


@skill_app.command("list")
def skill_list(
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """List installed skills (global + project)."""
    from rich.table import Table

    rows = _list_installed()
    if json_out:
        payload = [
            {
                "name": sk.name,
                "scope": scope,
                "description": sk.description,
                "license": sk.license,
                "compatibility": sk.compatibility,
                "path": str(p),
            }
            for sk, scope, p in rows
        ]
        typer.echo(json.dumps(payload, indent=2))
        return
    table = Table(title="Installed skills")
    for col in ("name", "scope", "license", "description"):
        table.add_column(col)
    for sk, scope, _p in rows:
        table.add_row(sk.name, scope, sk.license or "-", sk.description)
    console.print(table)


@skill_app.command("show")
def skill_show(name: str = typer.Argument(...)) -> None:
    """Print a skill's SKILL.md and resolved scope."""
    for sk, scope, p in _list_installed():
        if sk.name == name:
            typer.echo(f"# scope: {scope}")
            typer.echo(f"# path:  {p}")
            typer.echo("")
            typer.echo((p / "SKILL.md").read_text(encoding="utf-8"))
            return
    typer.echo(f"Skill not found: {name}", err=True)
    raise typer.Exit(code=1)


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

    config_path = _config_path()
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


def _maybe_execvp_into_docker(*, model_override: Optional[str]) -> None:
    """Re-exec into `docker run` if sandbox.mode is docker for this subcommand.

    Returns without doing anything if any of these holds:
      - We are already running inside the sandbox (BLUECLAW_SANDBOX_MODE env set
        by the host-side launcher). Prevents infinite re-launch recursion.
      - blueclaw.yaml is missing or fails to load (let the regular command path
        surface that error with its existing message).
      - sandbox.mode is 'inprocess'.
      - The subcommand belongs on the host (per launcher routing table).
      - Docker is unavailable AND sandbox.on_unavailable == 'fallback'.

    Exits via SystemExit if the user requested docker mode but the daemon or
    image is missing under 'error' policy.
    """
    # In-container guard: if the launcher already placed us inside the sandbox,
    # don't try to re-launch a second container.
    if os.environ.get("BLUECLAW_SANDBOX_MODE") == "docker":
        return

    from blueclaw.session import load_config  # lazy: matches existing pattern

    # Cheap pre-filter: skip entirely for host-only subcommands so we don't
    # trigger config-loading side effects for commands that never sandbox.
    subcommand = normalize_subcommand(sys.argv)
    if not should_sandbox_subcommand(subcommand):
        return

    config_path = _config_path()
    if not config_path.exists():
        return
    try:
        config = load_config(config_path, model_override=model_override)
    except Exception:
        return
    project_root = config_path.resolve().parent
    decision = decide_launch(
        sandbox_cfg=config.sandbox,
        provider=config.provider,
        argv=sys.argv,
        project_root=project_root,
    )
    if decision is not None:
        # Visible signal so users can confirm the sandbox actually fired.
        print(f"→ blueclaw sandbox: docker ({decision.image})", file=sys.stderr)
        os.execvp(decision.argv[0], decision.argv)
        # execvp replaces the process on success; reaching here means it
        # returned (only possible under test mocks). Exit so the caller
        # doesn't continue into the in-process subcommand.
        raise SystemExit(0)


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
    # Sandbox launcher: re-exec into docker if configured. Never returns when it fires.
    _maybe_execvp_into_docker(model_override=model)
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

    config_path = _config_path()
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
            console.print(f"[dim]attached:[/dim] {att.path} ({att.mime_type})")
        for token, reason in failed:
            console.print(f"[yellow]could not attach[/yellow] {token}: {reason}")
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


def _ensure_gitignore_entries(project_root: Path) -> None:
    """Add .env.docker patterns to .gitignore (idempotent)."""
    gi = project_root / ".gitignore"
    needed = [".env", ".env.docker", ".env.*"]
    existing = gi.read_text().splitlines() if gi.exists() else []
    missing = [p for p in needed if p not in existing]
    if not missing:
        return
    block = ["", "# blueclaw: never commit dotenv files (may contain secrets)"]
    block += missing
    with gi.open("a") as f:
        if existing and existing[-1] != "":
            f.write("\n")
        f.write("\n".join(block) + "\n")


@app.command()
def init() -> None:
    """Initialize a blueclaw workspace."""
    workspace = Workspace(DEFAULT_WORKSPACE)

    # Create CONTEXT.md with default content if missing
    if not workspace.context_path.exists():
        workspace.write_context(
            "# Workspace Context\n\n## Preferences\n\n## Projects\n"
        )

    # Create SOUL.md with default identity if missing (user-editable persona)
    if not workspace.soul_path.exists():
        workspace.soul_path.write_text(
            "# Soul\n\n"
            "I am blueclaw, a terminal automation agent.\n\n"
            "## Personality\n\n"
            "- Concise and direct\n"
            "- Curious and eager to learn\n"
            "- Honest about uncertainty\n\n"
            "## Values\n\n"
            "- Accuracy over speed\n"
            "- Transparency in actions\n"
            "- Respect the user's time\n\n"
            "## Communication Style\n\n"
            "- Lead with the answer or action, not the reasoning\n"
            "- Ask clarifying questions only when truly ambiguous\n"
            "- No filler, no preamble\n"
        )

    # Create config yaml if missing
    config_path = _config_path()
    if not config_path.exists():
        config_path.write_text(
            "model:\n  provider: anthropic\n  model_id: claude-sonnet-4-6\n\n"
            "workspace:\n  path: ~/blueclaw/workspace/\n"
            "  trace_retention_days: 30\n\n"
            "tools:\n  - web\n  - shell\n  - pdf\n\nallowlist_domains: []\n"
        )

    _ensure_gitignore_entries(Path.cwd())

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

    config_path = _config_path()
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

        config = load_config(_config_path(), model_override=model)
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

    uvicorn.run(app, host=_default_bind_host(), port=port, log_level="warning")


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

    config = load_config(_config_path())
    days = older_than if older_than is not None else config.trace_retention_days
    workspace = Workspace(DEFAULT_WORKSPACE)
    count = workspace.purge_old_traces(days, dry_run=dry_run)
    verb = "Would delete" if dry_run else "Deleted"
    console.print(f"{verb} {count} trace(s) older than {days} days.")


@app.command()
def serve(
    host: Optional[str] = typer.Option(
        None,
        help="Bind host (default: 127.0.0.1 on host, 0.0.0.0 inside docker sandbox)",
    ),
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

    config = load_config(_config_path(), model_override=model)
    if max_concurrent is not None:
        config = config.model_copy(update={"max_concurrent_runs": max_concurrent})
    workspace = Workspace(config.workspace_path)
    server_app = create_server_app(config, workspace, cors_origin=cors_origin)
    resolved_host = host or _default_bind_host()
    console.print(
        f"[bold]blueclaw serve[/bold] listening on http://{resolved_host}:{port}"
    )
    uvicorn.run(server_app, host=resolved_host, port=port)


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

    config = load_config(_config_path(), model_override=model or spec.model)
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


# --- Sandbox commands ---

sandbox_app = typer.Typer(help="Manage the docker sandbox runtime image.")
app.add_typer(sandbox_app, name="sandbox")


@sandbox_app.command("build")
def sandbox_build(
    no_cache: bool = typer.Option(
        False, "--no-cache", help="Pass --no-cache to docker build"
    ),
    platform: Optional[str] = typer.Option(
        None, "--platform", help="Pass --platform=<arch> to docker build"
    ),
) -> None:
    """Build the blueclaw runtime image from docker/Dockerfile."""
    cfg = SandboxConfig()
    tag = resolve_image_tag(cfg)
    repo_root = Path(__file__).resolve().parent.parent
    dockerfile = repo_root / "docker" / "Dockerfile"
    if not dockerfile.exists():
        typer.echo(f"Dockerfile not found at {dockerfile}", err=True)
        raise typer.Exit(2)
    cmd = ["docker", "build", "-t", tag, "-f", str(dockerfile)]
    if no_cache:
        cmd.append("--no-cache")
    if platform:
        cmd.append(f"--platform={platform}")
    cmd.append(str(repo_root))
    typer.echo(f"Building {tag}...")
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        typer.echo("docker build failed", err=True)
        raise typer.Exit(result.returncode)
    typer.echo(f"Built {tag}")


@sandbox_app.command("doctor")
def sandbox_doctor(
    json_output: bool = typer.Option(
        False, "--json", help="Emit machine-readable JSON"
    ),
) -> None:
    """Diagnose the docker sandbox configuration on this host."""
    cfg = SandboxConfig()
    tag = resolve_image_tag(cfg)
    docker_ok = docker_available()
    image_ok = image_exists(tag) if docker_ok else False

    report = {
        "docker_available": docker_ok,
        "image_tag": tag,
        "image_present": image_ok,
    }
    if json_output:
        typer.echo(_json.dumps(report, indent=2))
    else:
        typer.echo(f"docker: {'ok' if docker_ok else 'not available'}")
        typer.echo(
            f"image:  {tag} "
            f"{'(present)' if image_ok else '(MISSING - run `blueclaw sandbox build`)'}"
        )
    if not docker_ok or not image_ok:
        raise typer.Exit(1)
