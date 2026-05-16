"""Host-side sandbox decisions: editable detect, env compose, docker argv, execvp."""

from __future__ import annotations

import importlib.metadata
import json
import os
from pathlib import Path
from urllib.parse import urlparse

from blueclaw.dotenv import load_dotenv_files
from blueclaw.models import SandboxConfig


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
