"""Skill discovery — path resolution that feeds Strands' AgentSkills plugin."""

from __future__ import annotations

from pathlib import Path


def find_project_root(start: Path | None = None) -> Path | None:
    """Walk up from `start` (or cwd) looking for a directory with blueclaw.yaml.

    Returns the directory containing blueclaw.yaml, or None if no such
    directory is found before reaching the filesystem root.
    """
    here = (start or Path.cwd()).resolve()
    for candidate in [here, *here.parents]:
        if (candidate / "blueclaw.yaml").exists():
            return candidate
    return None


def default_global_dir() -> Path:
    return Path.home() / "blueclaw" / "skills"


def default_project_dir() -> Path | None:
    root = find_project_root()
    return root / ".blueclaw" / "skills" if root else None


def resolved_skill_paths(
    global_dir: Path | None,
    project_dir: Path | None,
) -> list[Path]:
    """Return one Path per discoverable skill, project shadowing global.

    Each returned path is a directory expected to contain SKILL.md. We do
    not parse SKILL.md here — that's Strands' job. We only enumerate dirs
    and apply project-precedence on name collisions.
    """
    by_name: dict[str, Path] = {}

    def collect(root: Path | None) -> None:
        if not root or not root.exists():
            return
        for child in sorted(root.iterdir()):
            if child.is_dir():
                by_name[child.name] = child

    collect(global_dir)
    collect(project_dir)  # overwrites global on name collision
    return list(by_name.values())
