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
    """Return the user-wide skill directory: ~/blueclaw/skills."""
    return Path.home() / "blueclaw" / "skills"


def default_project_dir(start: Path | None = None) -> Path | None:
    """Return <project-root>/.blueclaw/skills, or None if no project found.

    ``start`` is forwarded to ``find_project_root``; defaults to cwd.
    Tests should pass an explicit ``start`` (or mock the function) rather
    than relying on the process's working directory.
    """
    root = find_project_root(start=start)
    return root / ".blueclaw" / "skills" if root else None


def resolved_skill_paths(
    global_dir: Path | None,
    project_dir: Path | None,
) -> list[Path]:
    """Return one Path per discoverable skill, project shadowing global.

    Each returned path is a directory expected to contain SKILL.md. We do
    not parse SKILL.md here — that's Strands' job. We only enumerate dirs
    and apply project-precedence on name collisions.

    Return order: skills appear in the order they were discovered. A
    project skill that shadows a global one keeps the position the global
    entry first occupied (Python dict insertion order). Within a single
    scope, names are sorted alphabetically.
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
