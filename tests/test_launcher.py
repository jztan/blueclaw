"""Tests for blueclaw.launcher — host-side sandbox decisions."""

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from blueclaw.launcher import (
    BUILTIN_ENV_ALLOWLIST,
    NetworkValidationError,
    build_docker_argv,
    compose_env,
    detect_editable_source,
    validate_network_model,
)
from blueclaw.models import ExtraMount, SandboxConfig


class TestDetectEditableSource:
    def test_no_dist_returns_none(self):
        with patch("blueclaw.launcher.importlib.metadata.distribution") as m:
            m.side_effect = Exception("no dist")
            assert detect_editable_source() is None

    def test_no_direct_url_returns_none(self, tmp_path):
        dist = MagicMock()
        dist.locate_file.side_effect = FileNotFoundError
        dist.read_text.return_value = None
        with patch(
            "blueclaw.launcher.importlib.metadata.distribution", return_value=dist
        ):
            assert detect_editable_source() is None

    def test_non_editable_returns_none(self, tmp_path):
        # direct_url.json exists but dir_info.editable is False (or absent)
        dist = MagicMock()
        dist.read_text.return_value = json.dumps(
            {"url": "file:///some/path", "dir_info": {}}
        )
        with patch(
            "blueclaw.launcher.importlib.metadata.distribution", return_value=dist
        ):
            assert detect_editable_source() is None

    def test_editable_returns_path(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        dist = MagicMock()
        dist.read_text.return_value = json.dumps(
            {"url": src.as_uri(), "dir_info": {"editable": True}}
        )
        with patch(
            "blueclaw.launcher.importlib.metadata.distribution", return_value=dist
        ):
            assert detect_editable_source() == src.resolve()

    def test_editable_with_missing_source_dir_returns_none(self, tmp_path):
        dist = MagicMock()
        dist.read_text.return_value = json.dumps(
            {"url": (tmp_path / "gone").as_uri(), "dir_info": {"editable": True}}
        )
        with patch(
            "blueclaw.launcher.importlib.metadata.distribution", return_value=dist
        ):
            assert detect_editable_source() is None


class TestComposeEnv:
    def test_allowlist_inherited_from_host(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        monkeypatch.setenv("UNRELATED_VAR", "nope")
        result = compose_env(SandboxConfig(), project_root=tmp_path, home=tmp_path)
        assert result["ANTHROPIC_API_KEY"] == "sk-test"
        assert "UNRELATED_VAR" not in result

    def test_missing_allowlist_vars_silently_skipped(self, monkeypatch, tmp_path):
        for v in BUILTIN_ENV_ALLOWLIST:
            monkeypatch.delenv(v, raising=False)
        assert compose_env(SandboxConfig(), project_root=tmp_path, home=tmp_path) == {}

    def test_user_env_loaded_from_home(self, monkeypatch, tmp_path):
        for v in BUILTIN_ENV_ALLOWLIST:
            monkeypatch.delenv(v, raising=False)
        (tmp_path / "blueclaw").mkdir()
        (tmp_path / "blueclaw" / ".env").write_text("USER_KEY=fromHome\n")
        result = compose_env(SandboxConfig(), project_root=tmp_path, home=tmp_path)
        assert result == {"USER_KEY": "fromHome"}

    def test_project_env_overrides_user_env(self, monkeypatch, tmp_path):
        for v in BUILTIN_ENV_ALLOWLIST:
            monkeypatch.delenv(v, raising=False)
        (tmp_path / "blueclaw").mkdir()
        (tmp_path / "blueclaw" / ".env").write_text("KEY=fromHome\n")
        (tmp_path / ".env.docker").write_text("KEY=fromProject\n")
        result = compose_env(SandboxConfig(), project_root=tmp_path, home=tmp_path)
        assert result["KEY"] == "fromProject"

    def test_extra_env_overrides_files(self, monkeypatch, tmp_path):
        for v in BUILTIN_ENV_ALLOWLIST:
            monkeypatch.delenv(v, raising=False)
        (tmp_path / ".env.docker").write_text("KEY=fromFile\n")
        cfg = SandboxConfig(extra_env={"KEY": "fromYaml"})
        result = compose_env(cfg, project_root=tmp_path, home=tmp_path)
        assert result["KEY"] == "fromYaml"

    def test_extra_env_at_host_passthrough(self, monkeypatch, tmp_path):
        # Semantics: when extra_env[KEY] == "@host", the launcher forwards
        # the host env var named KEY (or omits it if unset).
        for v in BUILTIN_ENV_ALLOWLIST:
            monkeypatch.delenv(v, raising=False)
        monkeypatch.setenv("MY_HOST_VAR", "hostvalue")
        cfg = SandboxConfig(extra_env={"MY_HOST_VAR": "@host"})
        result = compose_env(cfg, project_root=tmp_path, home=tmp_path)
        assert result["MY_HOST_VAR"] == "hostvalue"

    def test_extra_env_at_host_missing_omitted(self, monkeypatch, tmp_path):
        for v in BUILTIN_ENV_ALLOWLIST:
            monkeypatch.delenv(v, raising=False)
        monkeypatch.delenv("NEVER_SET", raising=False)
        cfg = SandboxConfig(extra_env={"NEVER_SET": "@host"})
        result = compose_env(cfg, project_root=tmp_path, home=tmp_path)
        assert "NEVER_SET" not in result

    def test_env_files_override_replaces_defaults(self, monkeypatch, tmp_path):
        for v in BUILTIN_ENV_ALLOWLIST:
            monkeypatch.delenv(v, raising=False)
        (tmp_path / "blueclaw").mkdir()
        (tmp_path / "blueclaw" / ".env").write_text("DEFAULT_KEY=fromHome\n")
        custom = tmp_path / "custom.env"
        custom.write_text("CUSTOM_KEY=fromCustom\n")
        cfg = SandboxConfig(env_files=[custom])
        result = compose_env(cfg, project_root=tmp_path, home=tmp_path)
        assert "DEFAULT_KEY" not in result
        assert result["CUSTOM_KEY"] == "fromCustom"

    def test_env_files_empty_disables_loading(self, monkeypatch, tmp_path):
        for v in BUILTIN_ENV_ALLOWLIST:
            monkeypatch.delenv(v, raising=False)
        (tmp_path / "blueclaw").mkdir()
        (tmp_path / "blueclaw" / ".env").write_text("KEY=v\n")
        cfg = SandboxConfig(env_files=[])
        result = compose_env(cfg, project_root=tmp_path, home=tmp_path)
        assert result == {}


class TestValidateNetworkModel:
    def test_bridge_with_cloud_model_ok(self):
        validate_network_model(network="bridge", model_id="anthropic/claude-sonnet-4-6")

    def test_none_with_ollama_ok(self):
        validate_network_model(network="none", model_id="ollama/llama3")

    def test_none_with_cloud_rejected(self):
        with pytest.raises(NetworkValidationError, match="requires a local model"):
            validate_network_model(
                network="none", model_id="anthropic/claude-sonnet-4-6"
            )

    def test_none_with_bare_model_rejected(self):
        # model ids without 'ollama/' prefix assumed cloud
        with pytest.raises(NetworkValidationError):
            validate_network_model(network="none", model_id="claude-sonnet-4-6")


from blueclaw.launcher import docker_available


class TestDockerAvailable:
    def test_available_when_docker_info_ok(self, mocker):
        mock = mocker.patch("blueclaw.launcher.subprocess.run")
        mock.return_value.returncode = 0
        assert docker_available() is True

    def test_unavailable_when_nonzero(self, mocker):
        mock = mocker.patch("blueclaw.launcher.subprocess.run")
        mock.return_value.returncode = 1
        assert docker_available() is False

    def test_unavailable_when_timeout(self, mocker):
        import subprocess as sp

        mocker.patch(
            "blueclaw.launcher.subprocess.run",
            side_effect=sp.TimeoutExpired(cmd="docker info", timeout=5),
        )
        assert docker_available() is False

    def test_unavailable_when_executable_missing(self, mocker):
        mocker.patch(
            "blueclaw.launcher.subprocess.run",
            side_effect=FileNotFoundError("docker"),
        )
        assert docker_available() is False


from blueclaw.launcher import resolve_image_tag, image_digest


class TestResolveImageTag:
    def test_user_override_wins(self, mocker):
        cfg = SandboxConfig(image="custom/img:1")
        assert resolve_image_tag(cfg) == "custom/img:1"

    def test_release_tag_when_not_editable(self, mocker):
        mocker.patch("blueclaw.launcher.detect_editable_source", return_value=None)
        mocker.patch(
            "blueclaw.launcher.importlib.metadata.version", return_value="2.5.0"
        )
        assert resolve_image_tag(SandboxConfig()) == "blueclaw/runtime:2.5.0"

    def test_dev_tag_when_editable(self, mocker, tmp_path):
        mocker.patch("blueclaw.launcher.detect_editable_source", return_value=tmp_path)
        mocker.patch("blueclaw.launcher._git_short_sha", return_value="abc1234")
        assert resolve_image_tag(SandboxConfig()) == "blueclaw/runtime:dev-abc1234"

    def test_dev_tag_falls_back_to_nogit(self, mocker, tmp_path):
        mocker.patch("blueclaw.launcher.detect_editable_source", return_value=tmp_path)
        mocker.patch("blueclaw.launcher._git_short_sha", return_value=None)
        assert resolve_image_tag(SandboxConfig()) == "blueclaw/runtime:dev-nogit"

    def test_release_tag_returns_unknown_when_dist_missing(self, mocker):
        import importlib.metadata as im

        mocker.patch("blueclaw.launcher.detect_editable_source", return_value=None)
        mocker.patch(
            "blueclaw.launcher.importlib.metadata.version",
            side_effect=im.PackageNotFoundError("blueclaw"),
        )
        assert resolve_image_tag(SandboxConfig()) == "blueclaw/runtime:unknown"

    def test_dev_tag_rejects_invalid_sha(self, mocker, tmp_path):
        # If git output is malformed (newline mid-string, garbage), fall back to nogit.
        mocker.patch("blueclaw.launcher.detect_editable_source", return_value=tmp_path)
        mock = mocker.patch("blueclaw.launcher.subprocess.run")
        mock.return_value.returncode = 0
        mock.return_value.stdout = "abc\n1234\n"  # newline mid-output
        assert resolve_image_tag(SandboxConfig()) == "blueclaw/runtime:dev-nogit"


class TestImageDigest:
    def test_inspect_returns_digest(self, mocker):
        mock = mocker.patch("blueclaw.launcher.subprocess.run")
        mock.return_value.returncode = 0
        mock.return_value.stdout = "sha256:abc123\n"
        assert image_digest("blueclaw/runtime:2.5.0") == "sha256:abc123"

    def test_missing_image_returns_none(self, mocker):
        mock = mocker.patch("blueclaw.launcher.subprocess.run")
        mock.return_value.returncode = 1
        mock.return_value.stdout = ""
        assert image_digest("blueclaw/runtime:missing") is None


def _basic_cfg(**kw):
    return SandboxConfig(**kw)


class TestBuildDockerArgv:
    def test_includes_image_and_subcommand(self, tmp_path):
        argv = build_docker_argv(
            cfg=_basic_cfg(),
            image="blueclaw/runtime:test",
            env={},
            workspace=tmp_path,
            project_root=tmp_path,
            user_skills=tmp_path / "skills",
            project_skills=None,
            editable_source=None,
            inner_argv=["run", "hello"],
            interactive=False,
            publish_ports=[],
            digest=None,
        )
        assert argv[0:2] == ["docker", "run"]
        assert "--rm" in argv
        assert "blueclaw/runtime:test" in argv
        assert argv[-2:] == ["run", "hello"]

    def test_security_flags_present(self, tmp_path):
        argv = build_docker_argv(
            cfg=_basic_cfg(),
            image="img",
            env={},
            workspace=tmp_path,
            project_root=tmp_path,
            user_skills=tmp_path / "skills",
            project_skills=None,
            editable_source=None,
            inner_argv=["run"],
            interactive=False,
            publish_ports=[],
            digest=None,
        )
        assert "--security-opt" in argv and "no-new-privileges" in argv
        assert "--cap-drop" in argv and "ALL" in argv
        assert "--read-only" in argv

    def test_resource_caps(self, tmp_path):
        cfg = _basic_cfg(cpu=2.5, memory_mb=2048, pids=256)
        argv = build_docker_argv(
            cfg=cfg,
            image="img",
            env={},
            workspace=tmp_path,
            project_root=tmp_path,
            user_skills=tmp_path / "skills",
            project_skills=None,
            editable_source=None,
            inner_argv=["run"],
            interactive=False,
            publish_ports=[],
            digest=None,
        )
        assert "--cpus=2.5" in argv
        assert "--memory=2048m" in argv
        assert "--pids-limit=256" in argv

    def test_user_host_resolves_to_uid_gid(self, tmp_path, mocker):
        mocker.patch("blueclaw.launcher.os.getuid", return_value=501)
        mocker.patch("blueclaw.launcher.os.getgid", return_value=20)
        argv = build_docker_argv(
            cfg=_basic_cfg(user="host"),
            image="img",
            env={},
            workspace=tmp_path,
            project_root=tmp_path,
            user_skills=tmp_path / "skills",
            project_skills=None,
            editable_source=None,
            inner_argv=["run"],
            interactive=False,
            publish_ports=[],
            digest=None,
        )
        assert "--user" in argv
        i = argv.index("--user")
        assert argv[i + 1] == "501:20"

    def test_user_explicit_uses_value(self, tmp_path):
        argv = build_docker_argv(
            cfg=_basic_cfg(user="1000:1000"),
            image="img",
            env={},
            workspace=tmp_path,
            project_root=tmp_path,
            user_skills=tmp_path / "skills",
            project_skills=None,
            editable_source=None,
            inner_argv=["run"],
            interactive=False,
            publish_ports=[],
            digest=None,
        )
        i = argv.index("--user")
        assert argv[i + 1] == "1000:1000"

    def test_network_modes(self, tmp_path):
        for mode in ("bridge", "none"):
            argv = build_docker_argv(
                cfg=_basic_cfg(network=mode),
                image="img",
                env={},
                workspace=tmp_path,
                project_root=tmp_path,
                user_skills=tmp_path / "skills",
                project_skills=None,
                editable_source=None,
                inner_argv=["run"],
                interactive=False,
                publish_ports=[],
                digest=None,
            )
            assert f"--network={mode}" in argv

    def test_workspace_mount(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        argv = build_docker_argv(
            cfg=_basic_cfg(),
            image="img",
            env={},
            workspace=ws,
            project_root=tmp_path,
            user_skills=tmp_path / "skills",
            project_skills=None,
            editable_source=None,
            inner_argv=["run"],
            interactive=False,
            publish_ports=[],
            digest=None,
        )
        assert f"--mount=type=bind,source={ws},target=/workspace,readonly=false" in argv

    def test_editable_source_mounted_with_pythonpath(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        argv = build_docker_argv(
            cfg=_basic_cfg(),
            image="img",
            env={},
            workspace=tmp_path,
            project_root=tmp_path,
            user_skills=tmp_path / "skills",
            project_skills=None,
            editable_source=src,
            inner_argv=["run"],
            interactive=False,
            publish_ports=[],
            digest=None,
        )
        assert any(
            f"source={src},target=/opt/blueclaw-src,readonly=true" in a for a in argv
        )
        assert "--env=PYTHONPATH=/opt/blueclaw-src" in argv

    def test_user_skills_mounted_ro(self, tmp_path):
        skills = tmp_path / "skills"
        skills.mkdir()
        argv = build_docker_argv(
            cfg=_basic_cfg(),
            image="img",
            env={},
            workspace=tmp_path,
            project_root=tmp_path,
            user_skills=skills,
            project_skills=None,
            editable_source=None,
            inner_argv=["run"],
            interactive=False,
            publish_ports=[],
            digest=None,
        )
        assert any(
            f"source={skills},target=/home/blueclaw/skills,readonly=true" in a
            for a in argv
        )

    def test_project_skills_omitted_when_absent(self, tmp_path):
        argv = build_docker_argv(
            cfg=_basic_cfg(),
            image="img",
            env={},
            workspace=tmp_path,
            project_root=tmp_path,
            user_skills=tmp_path / "skills",
            project_skills=None,
            editable_source=None,
            inner_argv=["run"],
            interactive=False,
            publish_ports=[],
            digest=None,
        )
        assert not any("/project/.blueclaw/skills" in a for a in argv)

    def test_extra_mounts_included(self, tmp_path):
        cfg = _basic_cfg(
            extra_mounts=[
                ExtraMount(host=str(tmp_path / "x"), container="/mnt/x", mode="rw")
            ]
        )
        argv = build_docker_argv(
            cfg=cfg,
            image="img",
            env={},
            workspace=tmp_path,
            project_root=tmp_path,
            user_skills=tmp_path / "skills",
            project_skills=None,
            editable_source=None,
            inner_argv=["run"],
            interactive=False,
            publish_ports=[],
            digest=None,
        )
        assert any(
            "target=/mnt/x,readonly=false" in a and str(tmp_path / "x") in a
            for a in argv
        )

    def test_env_emitted_as_env_flags(self, tmp_path):
        argv = build_docker_argv(
            cfg=_basic_cfg(),
            image="img",
            env={"FOO": "bar", "BAZ": "qux"},
            workspace=tmp_path,
            project_root=tmp_path,
            user_skills=tmp_path / "skills",
            project_skills=None,
            editable_source=None,
            inner_argv=["run"],
            interactive=False,
            publish_ports=[],
            digest=None,
        )
        assert "--env=FOO=bar" in argv
        assert "--env=BAZ=qux" in argv

    def test_sandbox_metadata_env_vars(self, tmp_path):
        argv = build_docker_argv(
            cfg=_basic_cfg(),
            image="blueclaw/runtime:test",
            env={},
            workspace=tmp_path,
            project_root=tmp_path,
            user_skills=tmp_path / "skills",
            project_skills=None,
            editable_source=None,
            inner_argv=["run"],
            interactive=False,
            publish_ports=[],
            digest="sha256:abc",
        )
        assert "--env=BLUECLAW_SANDBOX_MODE=docker" in argv
        assert "--env=BLUECLAW_SANDBOX_IMAGE=blueclaw/runtime:test" in argv
        assert "--env=BLUECLAW_SANDBOX_DIGEST=sha256:abc" in argv

    def test_interactive_adds_it_flags(self, tmp_path):
        argv = build_docker_argv(
            cfg=_basic_cfg(),
            image="img",
            env={},
            workspace=tmp_path,
            project_root=tmp_path,
            user_skills=tmp_path / "skills",
            project_skills=None,
            editable_source=None,
            inner_argv=["run"],
            interactive=True,
            publish_ports=[],
            digest=None,
        )
        assert "-i" in argv and "-t" in argv

    def test_publish_ports(self, tmp_path):
        argv = build_docker_argv(
            cfg=_basic_cfg(),
            image="img",
            env={},
            workspace=tmp_path,
            project_root=tmp_path,
            user_skills=tmp_path / "skills",
            project_skills=None,
            editable_source=None,
            inner_argv=["serve"],
            interactive=False,
            publish_ports=[8420],
            digest=None,
        )
        assert "--publish=8420:8420" in argv


from blueclaw.launcher import should_sandbox_subcommand


class TestShouldSandboxSubcommand:
    @pytest.mark.parametrize("cmd", ["run", "serve", "test", "trace ui", ""])
    def test_container_commands(self, cmd):
        # empty string = no subcommand = interactive
        assert should_sandbox_subcommand(cmd) is True

    @pytest.mark.parametrize(
        "cmd",
        [
            "sandbox build",
            "sandbox doctor",
            "skill install",
            "skill uninstall",
            "skill list",
            "skill show",
            "init",
            "history",
            "trace list",
            "trace show",
            "trace explain",
            "trace graph",
            "trace diff",
            "trace replay",
            "trace timeline",
            "trace stats",
        ],
    )
    def test_host_commands(self, cmd):
        assert should_sandbox_subcommand(cmd) is False


from blueclaw.launcher import normalize_subcommand


class TestNormalizeSubcommand:
    def test_no_args(self):
        assert normalize_subcommand(["blueclaw"]) == ""

    def test_run(self):
        assert normalize_subcommand(["blueclaw", "run", "hello"]) == "run"

    def test_two_word(self):
        assert (
            normalize_subcommand(["blueclaw", "trace", "ui", "--port", "9000"])
            == "trace ui"
        )

    def test_stops_at_flag(self):
        assert normalize_subcommand(["blueclaw", "run", "--model", "x"]) == "run"

    def test_two_word_sandbox(self):
        assert normalize_subcommand(["blueclaw", "sandbox", "build"]) == "sandbox build"


from blueclaw.launcher import LauncherDecision, decide_launch


class TestDecideLaunch:
    def _ws_layout(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        (tmp_path / "blueclaw" / "workspace").mkdir(parents=True)
        (tmp_path / "blueclaw" / "skills").mkdir()
        return tmp_path

    def test_inprocess_returns_none(self, tmp_path, monkeypatch):
        self._ws_layout(tmp_path, monkeypatch)
        cfg = SandboxConfig(mode="inprocess")
        decision = decide_launch(
            sandbox_cfg=cfg,
            model_id="anthropic/claude-sonnet-4-6",
            argv=["blueclaw", "run", "hello"],
            project_root=tmp_path,
        )
        assert decision is None

    def test_host_command_returns_none(self, tmp_path, monkeypatch):
        self._ws_layout(tmp_path, monkeypatch)
        cfg = SandboxConfig(mode="docker")
        decision = decide_launch(
            sandbox_cfg=cfg,
            model_id="anthropic/claude-sonnet-4-6",
            argv=["blueclaw", "skill", "list"],
            project_root=tmp_path,
        )
        assert decision is None

    def test_container_command_returns_argv(self, tmp_path, monkeypatch, mocker):
        self._ws_layout(tmp_path, monkeypatch)
        mocker.patch("blueclaw.launcher.docker_available", return_value=True)
        mocker.patch("blueclaw.launcher.image_digest", return_value="sha256:deadbeef")
        mocker.patch("blueclaw.launcher.image_exists", return_value=True)
        cfg = SandboxConfig(mode="docker", image="blueclaw/runtime:test")
        decision = decide_launch(
            sandbox_cfg=cfg,
            model_id="anthropic/claude-sonnet-4-6",
            argv=["blueclaw", "run", "hello"],
            project_root=tmp_path,
        )
        assert isinstance(decision, LauncherDecision)
        assert decision.argv[0] == "docker"
        assert "blueclaw/runtime:test" in decision.argv

    def test_docker_unavailable_error_mode_raises(self, tmp_path, monkeypatch, mocker):
        self._ws_layout(tmp_path, monkeypatch)
        mocker.patch("blueclaw.launcher.docker_available", return_value=False)
        cfg = SandboxConfig(mode="docker", on_unavailable="error")
        with pytest.raises(SystemExit):
            decide_launch(
                sandbox_cfg=cfg,
                model_id="anthropic/claude-sonnet-4-6",
                argv=["blueclaw", "run"],
                project_root=tmp_path,
            )

    def test_docker_unavailable_fallback_returns_none(
        self, tmp_path, monkeypatch, mocker, capsys
    ):
        self._ws_layout(tmp_path, monkeypatch)
        mocker.patch("blueclaw.launcher.docker_available", return_value=False)
        cfg = SandboxConfig(mode="docker", on_unavailable="fallback")
        decision = decide_launch(
            sandbox_cfg=cfg,
            model_id="anthropic/claude-sonnet-4-6",
            argv=["blueclaw", "run"],
            project_root=tmp_path,
        )
        assert decision is None
        out = capsys.readouterr()
        assert "Docker unavailable" in out.err
        assert os.environ.get("BLUECLAW_SANDBOX_FALLBACK_REASON")

    def test_missing_image_errors_with_build_hint(
        self, tmp_path, monkeypatch, mocker, capsys
    ):
        self._ws_layout(tmp_path, monkeypatch)
        mocker.patch("blueclaw.launcher.docker_available", return_value=True)
        mocker.patch("blueclaw.launcher.image_exists", return_value=False)
        cfg = SandboxConfig(mode="docker", image="blueclaw/runtime:test")
        with pytest.raises(SystemExit):
            decide_launch(
                sandbox_cfg=cfg,
                model_id="anthropic/claude-sonnet-4-6",
                argv=["blueclaw", "run"],
                project_root=tmp_path,
            )
        out = capsys.readouterr()
        assert "sandbox build" in out.err

    def test_network_validation_runs(self, tmp_path, monkeypatch):
        self._ws_layout(tmp_path, monkeypatch)
        cfg = SandboxConfig(mode="docker", network="none")
        with pytest.raises(NetworkValidationError):
            decide_launch(
                sandbox_cfg=cfg,
                model_id="anthropic/claude-sonnet-4-6",
                argv=["blueclaw", "run"],
                project_root=tmp_path,
            )
