"""Host-side sandbox decisions: editable detect, env compose, docker argv, execvp."""

from __future__ import annotations

import importlib.metadata
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from blueclaw.dotenv import load_dotenv_files
from blueclaw.models import ExtraMount, SandboxConfig  # noqa: F401


def detect_editable_source() -> Path | None:
    """Return the source path if blueclaw is installed editable (PEP 660), else None.

    Reads dist-info/direct_url.json per PEP 610. An editable install is signaled
    by `dir_info.editable == True`. The url is `file://...` pointing at the source.
    """
    try:
        dist = importlib.metadata.distribution("blueclaw")
    except Exception:
        return None
    try:
        raw = dist.read_text("direct_url.json")
    except Exception:
        raw = None
    if not raw:
        return None
    try:
        meta = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not meta.get("dir_info", {}).get("editable"):
        return None
    url = meta.get("url")
    if not url or not url.startswith("file://"):
        return None
    src = Path(urlparse(url).path).resolve()
    if not src.exists():
        return None
    return src


BUILTIN_ENV_ALLOWLIST = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "OLLAMA_HOST",
    "BLUECLAW_API_KEY",
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "NO_PROXY",
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "TELEGRAM_BOT_TOKEN",
)


def compose_env(
    cfg: SandboxConfig,
    *,
    project_root: Path,
    home: Path,
) -> dict[str, str]:
    """Compose the env vars to forward to the agent.

    Precedence (low -> high):
      1. BUILTIN_ENV_ALLOWLIST read from the launcher's own env (os.environ).
      2. ~/blueclaw/.env (loaded if present; replaced if cfg.env_files set).
      3. <project_root>/.env (loaded if present; matches the host-side
         python-dotenv load at cli.py:23 so the same keys reach the container).
      4. <project_root>/.env.docker (loaded if present; replaced if cfg.env_files set).
      5. cfg.extra_env. Special form: value == "@host" means read the host env var
         of the same name; omit if unset.

    cfg.env_files, when explicitly provided, replaces (2)+(3)+(4) entirely.
    cfg.env_files == [] disables file loading.
    """
    env: dict[str, str] = {}

    # Layer 1: allowlist
    for name in BUILTIN_ENV_ALLOWLIST:
        if name in os.environ:
            env[name] = os.environ[name]

    # Layer 2+3+4: dotenv files
    if cfg.env_files is None:
        default_files = [
            home / "blueclaw" / ".env",
            project_root / ".env",
            project_root / ".env.docker",
        ]
        env.update(load_dotenv_files(default_files))
    else:
        env.update(load_dotenv_files(list(cfg.env_files)))

    # Layer 4: extra_env (with @host pass-through)
    for key, value in cfg.extra_env.items():
        if value == "@host":
            if key in os.environ:
                env[key] = os.environ[key]
        else:
            env[key] = value

    return env


class NetworkValidationError(ValueError):
    """Raised when network mode is incompatible with the selected model."""


def validate_network_model(*, network: str, provider: str) -> None:
    """Reject configurations that would fail silently inside the container."""
    if network == "none" and provider != "ollama":
        raise NetworkValidationError(
            f"network: none requires a local model; configured provider "
            f"{provider!r} needs network access"
        )


def docker_available(timeout: float = 5.0) -> bool:
    """Return True if `docker info` returns 0 within the timeout."""
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")


def _git_short_sha(repo: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--short=7", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if result.returncode != 0:
            return None
        sha = result.stdout.strip()
        if not sha or not _SHA_RE.fullmatch(sha):
            return None
        return sha
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def resolve_image_tag(cfg: SandboxConfig) -> str:
    """Resolve the image tag the launcher will run."""
    if cfg.image:
        return cfg.image
    src = detect_editable_source()
    if src is not None:
        sha = _git_short_sha(src) or "nogit"
        return f"blueclaw/runtime:dev-{sha}"
    try:
        version = importlib.metadata.version("blueclaw")
    except importlib.metadata.PackageNotFoundError:
        return "blueclaw/runtime:unknown"
    return f"blueclaw/runtime:{version}"


def image_digest(tag: str) -> str | None:
    """Return sha256 digest of a local image tag, or None if not present."""
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", "--format={{.Id}}", tag],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    if result.returncode != 0:
        return None
    digest = result.stdout.strip()
    return digest or None


def build_docker_argv(
    *,
    cfg: SandboxConfig,
    image: str,
    env: dict[str, str],
    workspace: Path,
    project_root: Path,
    user_skills: Path,
    project_skills: Path | None,
    chats_root: Path | None = None,
    editable_source: Path | None,
    inner_argv: list[str],
    interactive: bool,
    publish_ports: list[int],
    digest: str | None,
) -> list[str]:
    """Assemble the full `docker run ...` argv that the launcher will execvp into."""
    user = cfg.user
    if user == "host":
        user = f"{os.getuid()}:{os.getgid()}"

    argv: list[str] = ["docker", "run", "--rm"]
    if interactive:
        argv += ["-i", "-t"]

    # Security
    argv += [
        "--security-opt",
        "no-new-privileges",
        "--cap-drop",
        "ALL",
        "--read-only",
        "--tmpfs",
        "/tmp:size=256m",
        "--tmpfs",
        "/run:size=64m",
        # XDG cache + config + state under HOME — many tools (pdf-mcp, pip,
        # huggingface, etc.) write there. Ephemeral tmpfs keeps the root FS
        # read-only without forcing per-tool config overrides.
        "--tmpfs",
        "/home/blueclaw/.cache:size=256m",
        "--tmpfs",
        "/home/blueclaw/.config:size=32m",
        "--tmpfs",
        "/home/blueclaw/.local:size=64m",
        "--user",
        user,
        "--workdir",
        "/home/blueclaw/blueclaw/workspace",
        f"--network={cfg.network}",
        f"--cpus={cfg.cpu}",
        f"--memory={cfg.memory_mb}m",
        f"--pids-limit={cfg.pids}",
        "--env=PYTHONDONTWRITEBYTECODE=1",
    ]

    # Map host.docker.internal -> host gateway. Docker Desktop (mac/Windows)
    # provides this automatically, but native Linux Docker does not. Adding
    # the flag is harmless on Desktop and lets `OLLAMA_HOST` default work
    # uniformly. Skipped for `--network=host` and `--network=none` where the
    # name has no meaning.
    if cfg.network not in ("host", "none"):
        argv += ["--add-host=host.docker.internal:host-gateway"]

    # Sandbox metadata env vars (read by observer.py inside container)
    argv += [
        "--env=BLUECLAW_SANDBOX_MODE=docker",
        f"--env=BLUECLAW_SANDBOX_IMAGE={image}",
    ]
    if digest:
        argv += [f"--env=BLUECLAW_SANDBOX_DIGEST={digest}"]

    # Composed env
    for k, v in env.items():
        argv += [f"--env={k}={v}"]

    if editable_source is not None:
        argv += [
            f"--mount=type=bind,source={editable_source},"
            f"target=/opt/blueclaw-src,readonly=true",
            "--env=PYTHONPATH=/opt/blueclaw-src",
        ]

    # Mandatory mounts. Targets are chosen so that Path.home() / "blueclaw" / ...
    # resolves correctly inside the container (HOME=/home/blueclaw).
    #
    # IMPORTANT: the config file must be mounted *outside* the workspace mount.
    # Docker Desktop on macOS (VirtioFS) refuses to bind-mount a file into a
    # path that's inside another bind mount. We mount it at a sibling path
    # (/home/blueclaw/blueclaw.yaml) and tell the in-container blueclaw where
    # to find it via BLUECLAW_CONFIG.
    config_target = "/home/blueclaw/blueclaw.yaml"
    argv += [
        f"--mount=type=bind,source={workspace},"
        f"target=/home/blueclaw/blueclaw/workspace,readonly=false",
        f"--mount=type=bind,source={user_skills},"
        f"target=/home/blueclaw/blueclaw/skills,readonly=true",
        f"--mount=type=bind,source={project_root}/blueclaw.yaml,"
        f"target={config_target},readonly=true",
        f"--env=BLUECLAW_CONFIG={config_target}",
    ]
    if project_skills is not None:
        argv += [
            f"--mount=type=bind,source={project_skills},"
            f"target=/home/blueclaw/blueclaw/workspace/.blueclaw/skills,"
            f"readonly=true",
        ]
    if chats_root is not None:
        chats_root.mkdir(parents=True, exist_ok=True)
        argv += [
            f"--mount=type=bind,source={chats_root},"
            f"target=/home/blueclaw/blueclaw/chats,readonly=false",
        ]

    # Extra mounts
    for m in cfg.extra_mounts:
        readonly = "true" if m.mode == "ro" else "false"
        argv += [
            f"--mount=type=bind,source={os.path.expanduser(m.host)},"
            f"target={m.container},readonly={readonly}",
        ]

    # Port publishing
    for port in publish_ports:
        argv += [f"--publish={port}:{port}"]

    argv += [image, *inner_argv]
    return argv


# Commands that run inside the docker container when sandbox.mode == "docker".
# Everything else runs on the host.
_CONTAINER_COMMANDS = frozenset({"", "run", "serve", "telegram", "test", "trace ui"})


def should_sandbox_subcommand(subcommand: str) -> bool:
    """Decide whether a given (already-normalized) subcommand routes to the container.

    `subcommand` is the space-joined sequence of positional words before any flags,
    e.g. "run", "trace ui", "sandbox build", or "" for the no-subcommand
    interactive case.
    """
    return subcommand in _CONTAINER_COMMANDS


# Pairs of (first, second) that constitute a two-word subcommand. Add as needed.
_TWO_WORD_COMMANDS = frozenset(
    {("trace", "ui"), ("sandbox", "build"), ("sandbox", "doctor")}
    | {("skill", x) for x in ("install", "uninstall", "list", "show")}
    | {
        ("trace", x)
        for x in (
            "list",
            "show",
            "explain",
            "graph",
            "diff",
            "replay",
            "timeline",
            "stats",
        )
    }
)


def normalize_subcommand(argv: list[str]) -> str:
    """Extract the (one- or two-word) subcommand from argv. Empty string if none."""
    words = []
    for token in argv[1:]:
        if token.startswith("-"):
            break
        words.append(token)
        if len(words) == 2:
            break
    if len(words) >= 2 and (words[0], words[1]) in _TWO_WORD_COMMANDS:
        return f"{words[0]} {words[1]}"
    if words:
        return words[0]
    return ""


@dataclass(frozen=True)
class LauncherDecision:
    """Result of decide_launch when the agent should run inside docker."""

    argv: list[str]
    image: str


def image_exists(tag: str) -> bool:
    """Return True if a local image with `tag` exists."""
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", tag],
            capture_output=True,
            timeout=5,
            check=False,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def _parse_port_flag(argv: list[str], *, default: int) -> int:
    """Best-effort scan for --port N or -p N in argv."""
    for i, tok in enumerate(argv):
        if tok in ("--port", "-p") and i + 1 < len(argv):
            try:
                return int(argv[i + 1])
            except ValueError:
                pass
        if tok.startswith("--port="):
            try:
                return int(tok.split("=", 1)[1])
            except ValueError:
                pass
    return default


def decide_launch(
    *,
    sandbox_cfg: SandboxConfig,
    provider: str,
    argv: list[str],
    project_root: Path,
) -> LauncherDecision | None:
    """Return a LauncherDecision if we should execvp into docker; None otherwise."""
    if sandbox_cfg.mode != "docker":
        return None

    subcommand = normalize_subcommand(argv)
    if not should_sandbox_subcommand(subcommand):
        return None

    # Runtime validation — depends on provider which isn't in SandboxConfig itself.
    validate_network_model(network=sandbox_cfg.network, provider=provider)

    if not docker_available():
        if sandbox_cfg.on_unavailable == "fallback":
            print(
                "WARN: Docker unavailable; falling back to in-process sandbox",
                file=sys.stderr,
            )
            os.environ["BLUECLAW_SANDBOX_FALLBACK_REASON"] = "docker unavailable"
            return None
        print(
            "ERROR: sandbox.mode is 'docker' but Docker is unavailable. "
            "Set sandbox.on_unavailable: fallback to degrade to in-process, "
            "or fix your Docker installation.",
            file=sys.stderr,
        )
        raise SystemExit(2)

    image = resolve_image_tag(sandbox_cfg)
    if not image_exists(image):
        print(
            f"ERROR: image {image!r} not found.\n" f"  Run: blueclaw sandbox build",
            file=sys.stderr,
        )
        raise SystemExit(2)

    home = Path(os.path.expanduser("~"))
    workspace = home / "blueclaw" / "workspace"
    user_skills = home / "blueclaw" / "skills"
    user_skills.mkdir(parents=True, exist_ok=True)
    chats_root: Path | None = None
    if subcommand == "telegram":
        chats_root = home / "blueclaw" / "chats"

    project_skills: Path | None = project_root / ".blueclaw" / "skills"
    if project_skills is not None and not project_skills.exists():
        project_skills = None

    editable = detect_editable_source()
    digest = image_digest(image)
    env = compose_env(sandbox_cfg, project_root=project_root, home=home)

    # Inside the container, `localhost` is the container itself — not the host
    # running Ollama. Default OLLAMA_HOST to host.docker.internal so the agent
    # can reach the host Ollama daemon. Users can override via env/dotenv.
    # (The container also needs `--add-host=host.docker.internal:host-gateway`,
    # added in build_docker_argv.)
    if provider == "ollama" and "OLLAMA_HOST" not in env:
        env["OLLAMA_HOST"] = "http://host.docker.internal:11434"

    interactive = (
        sys.stdin.isatty() and sys.stdout.isatty() and subcommand in ("", "run")
    )

    publish_ports: list[int] = []
    if subcommand == "serve":
        publish_ports = [_parse_port_flag(argv, default=8420)]
    elif subcommand == "trace ui":
        publish_ports = [_parse_port_flag(argv, default=8421)]

    inner_argv = argv[1:]
    docker_argv = build_docker_argv(
        cfg=sandbox_cfg,
        image=image,
        env=env,
        workspace=workspace,
        project_root=project_root,
        user_skills=user_skills,
        project_skills=project_skills,
        chats_root=chats_root,
        editable_source=editable,
        inner_argv=inner_argv,
        interactive=interactive,
        publish_ports=publish_ports,
        digest=digest,
    )
    return LauncherDecision(argv=docker_argv, image=image)


def execvp_into(decision: LauncherDecision) -> None:
    """Replace the current process with docker run. Never returns on success."""
    os.execvp(decision.argv[0], decision.argv)
