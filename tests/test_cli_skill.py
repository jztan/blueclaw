import shutil
import subprocess
from pathlib import Path

from typer.testing import CliRunner

from blueclaw.cli import app

runner = CliRunner()


def _make_skill_dir(root: Path, name: str = "demo") -> Path:
    p = root / name
    p.mkdir(parents=True)
    (p / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: A demo skill\n---\n\nBody\n"
    )
    return p


def test_skill_install_local_path_with_yes(tmp_path, monkeypatch):
    src = _make_skill_dir(tmp_path / "src")
    target = tmp_path / "global"
    monkeypatch.setattr("blueclaw.cli._global_skills_dir", lambda: target)
    res = runner.invoke(app, ["skill", "install", str(src), "--yes"])
    assert res.exit_code == 0, res.output
    assert (target / "demo" / "SKILL.md").exists()


def test_skill_install_refuses_existing_without_force(tmp_path, monkeypatch):
    src = _make_skill_dir(tmp_path / "src")
    target = tmp_path / "global"
    (target / "demo").mkdir(parents=True)
    monkeypatch.setattr("blueclaw.cli._global_skills_dir", lambda: target)
    res = runner.invoke(app, ["skill", "install", str(src), "--yes"])
    assert res.exit_code != 0
    assert "exists" in res.output.lower()


def test_skill_install_force_overwrites(tmp_path, monkeypatch):
    src = _make_skill_dir(tmp_path / "src")
    target = tmp_path / "global"
    (target / "demo").mkdir(parents=True)
    (target / "demo" / "marker.txt").write_text("old")
    monkeypatch.setattr("blueclaw.cli._global_skills_dir", lambda: target)
    res = runner.invoke(app, ["skill", "install", str(src), "--yes", "--force"])
    assert res.exit_code == 0, res.output
    assert not (target / "demo" / "marker.txt").exists()
    assert (target / "demo" / "SKILL.md").exists()


def test_skill_install_propagates_validation_error(tmp_path, monkeypatch):
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "SKILL.md").write_text("no frontmatter\n")
    target = tmp_path / "global"
    monkeypatch.setattr("blueclaw.cli._global_skills_dir", lambda: target)
    res = runner.invoke(app, ["skill", "install", str(bad), "--yes"])
    assert res.exit_code != 0
    assert "frontmatter" in res.output.lower()


def test_skill_install_project_scope(tmp_path, monkeypatch):
    src = _make_skill_dir(tmp_path / "src")
    project = tmp_path / "project"
    project.mkdir()
    (project / "blueclaw.yaml").write_text("model: x\n")
    monkeypatch.chdir(project)
    res = runner.invoke(app, ["skill", "install", str(src), "--project", "--yes"])
    assert res.exit_code == 0, res.output
    assert (project / ".blueclaw" / "skills" / "demo" / "SKILL.md").exists()


def _fake_git_clone_factory(skill_template: Path):
    def fake_run(cmd, *args, **kwargs):
        dest = Path(cmd[-1])
        dest.mkdir(parents=True, exist_ok=True)
        for entry in skill_template.iterdir():
            if entry.is_dir():
                shutil.copytree(entry, dest / entry.name)
            else:
                shutil.copy2(entry, dest / entry.name)

        class R:
            returncode = 0
            stdout = ""
            stderr = ""

        return R()

    return fake_run


def test_skill_install_from_git_url(tmp_path, monkeypatch):
    template = tmp_path / "src" / "demo"
    template.mkdir(parents=True)
    (template / "SKILL.md").write_text(
        "---\nname: demo\ndescription: from git\n---\n\nbody\n"
    )
    target = tmp_path / "global"
    monkeypatch.setattr("blueclaw.cli._global_skills_dir", lambda: target)
    monkeypatch.setattr(subprocess, "run", _fake_git_clone_factory(template))
    res = runner.invoke(app, ["skill", "install", "https://example.com/r.git", "--yes"])
    assert res.exit_code == 0, res.output
    assert (target / "demo" / "SKILL.md").exists()


def test_skill_install_from_git_url_with_subdir(tmp_path, monkeypatch):
    template = tmp_path / "src"
    sub = template / "skills" / "demo"
    sub.mkdir(parents=True)
    (sub / "SKILL.md").write_text("---\nname: demo\ndescription: subdir\n---\n\nbody\n")
    target = tmp_path / "global"
    monkeypatch.setattr("blueclaw.cli._global_skills_dir", lambda: target)
    monkeypatch.setattr(subprocess, "run", _fake_git_clone_factory(template))
    res = runner.invoke(
        app,
        ["skill", "install", "https://example.com/r.git#skills/demo", "--yes"],
    )
    assert res.exit_code == 0, res.output
    assert (target / "demo" / "SKILL.md").exists()
