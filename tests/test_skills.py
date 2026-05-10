from pathlib import Path

from blueclaw.skills import (
    find_project_root,
    resolved_skill_paths,
)


def _write_skill_md(skill_dir: Path, name: str, description: str = "x") -> None:
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\nbody\n",
        encoding="utf-8",
    )


def test_find_project_root_walks_up(tmp_path):
    project = tmp_path / "proj"
    nested = project / "a" / "b"
    nested.mkdir(parents=True)
    (project / "blueclaw.yaml").write_text("model: anthropic/claude\n")
    assert find_project_root(start=nested) == project


def test_find_project_root_returns_none_when_missing(tmp_path):
    nested = tmp_path / "lonely"
    nested.mkdir()
    assert find_project_root(start=nested) is None


def test_resolved_skill_paths_global_only(tmp_path):
    g = tmp_path / "global"
    _write_skill_md(g / "alpha", "alpha")
    paths = resolved_skill_paths(global_dir=g, project_dir=None)
    assert [p.name for p in paths] == ["alpha"]


def test_resolved_skill_paths_project_shadows_global(tmp_path):
    g = tmp_path / "global"
    p = tmp_path / "project"
    _write_skill_md(g / "shared", "shared", description="from global")
    _write_skill_md(p / "shared", "shared", description="from project")
    paths = resolved_skill_paths(global_dir=g, project_dir=p)
    assert len(paths) == 1
    assert paths[0].is_relative_to(p)


def test_resolved_skill_paths_combines(tmp_path):
    g = tmp_path / "global"
    p = tmp_path / "project"
    _write_skill_md(g / "alpha", "alpha")
    _write_skill_md(p / "beta", "beta")
    paths = sorted(
        resolved_skill_paths(global_dir=g, project_dir=p), key=lambda x: x.name
    )
    assert [x.name for x in paths] == ["alpha", "beta"]


def test_resolved_skill_paths_missing_dirs(tmp_path):
    assert (
        resolved_skill_paths(
            global_dir=tmp_path / "nope-g",
            project_dir=tmp_path / "nope-p",
        )
        == []
    )


def test_resolved_skill_paths_skips_files(tmp_path):
    g = tmp_path / "global"
    g.mkdir()
    (g / "stray.txt").write_text("not a dir")
    _write_skill_md(g / "alpha", "alpha")
    paths = resolved_skill_paths(global_dir=g, project_dir=None)
    assert [p.name for p in paths] == ["alpha"]
