"""Host-side sandbox decisions: editable detect, env compose, docker argv, execvp."""

from __future__ import annotations

import importlib.metadata
import json
import os
import re
import subprocess
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
      3. <project_root>/.env.docker (loaded if present; replaced if cfg.env_files set).
      4. cfg.extra_env. Special form: value == "@host" means read the host env var
         of the same name; omit if unset.

    cfg.env_files, when explicitly provided, replaces (2)+(3) entirely.
    cfg.env_files == [] disables file loading.
    """
    env: dict[str, str] = {}

    # Layer 1: allowlist
    for name in BUILTIN_ENV_ALLOWLIST:
        if name in os.environ:
            env[name] = os.environ[name]

    # Layer 2+3: dotenv files
    if cfg.env_files is None:
        default_files = [
            home / "blueclaw" / ".env",
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


def validate_network_model(*, network: str, model_id: str) -> None:
    """Reject configurations that would fail silently inside the container."""
    if network == "none" and not model_id.startswith("ollama/"):
        raise NetworkValidationError(
            f"network: none requires a local model; configured model "
            f"{model_id!r} needs network access"
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
        "--user",
        user,
        "--workdir",
        "/workspace",
        f"--network={cfg.network}",
        f"--cpus={cfg.cpu}",
        f"--memory={cfg.memory_mb}m",
        f"--pids-limit={cfg.pids}",
        "--env=PYTHONDONTWRITEBYTECODE=1",
    ]

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

    # Mandatory mounts
    argv += [
        f"--mount=type=bind,source={workspace},target=/workspace,readonly=false",
        f"--mount=type=bind,source={user_skills},"
        f"target=/home/blueclaw/skills,readonly=true",
        f"--mount=type=bind,source={project_root}/blueclaw.yaml,"
        f"target=/project/blueclaw.yaml,readonly=true",
    ]
    if project_skills is not None:
        argv += [
            f"--mount=type=bind,source={project_skills},"
            f"target=/project/.blueclaw/skills,readonly=true",
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
_CONTAINER_COMMANDS = frozenset({"", "run", "serve", "test", "trace ui"})


def should_sandbox_subcommand(subcommand: str) -> bool:
    """Decide whether a given (already-normalized) subcommand routes to the container.

    `subcommand` is the space-joined sequence of positional words before any flags,
    e.g. "run", "trace ui", "sandbox build", or "" for the no-subcommand
    interactive case.
    """
    return subcommand in _CONTAINER_COMMANDS
