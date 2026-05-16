"""Tests for blueclaw.models.SandboxConfig — schema + validators."""

import pytest

from blueclaw.models import ExtraMount, SandboxConfig, SessionConfig


class TestSandboxConfigDefaults:
    def test_defaults(self):
        cfg = SandboxConfig()
        assert cfg.mode == "inprocess"
        assert cfg.network == "bridge"
        assert cfg.cpu == 1.0
        assert cfg.memory_mb == 1024
        assert cfg.pids == 512
        assert cfg.on_unavailable == "error"
        assert cfg.user == "host"
        assert cfg.image is None
        assert cfg.env_files is None
        assert cfg.extra_mounts == []
        assert cfg.extra_env == {}

    def test_session_config_includes_sandbox(self):
        sc = SessionConfig()
        assert sc.sandbox.mode == "inprocess"


class TestSandboxConfigValidators:
    def test_invalid_mode(self):
        with pytest.raises(ValueError):
            SandboxConfig(mode="bogus")

    def test_proxy_network_rejected(self):
        with pytest.raises(ValueError, match="reserved for v3"):
            SandboxConfig(network="proxy")

    def test_invalid_network(self):
        with pytest.raises(ValueError):
            SandboxConfig(network="bogus")

    def test_user_host(self):
        assert SandboxConfig(user="host").user == "host"

    def test_user_explicit_uid_gid(self):
        assert SandboxConfig(user="501:20").user == "501:20"

    def test_user_invalid_format(self):
        with pytest.raises(ValueError, match="uid:gid"):
            SandboxConfig(user="root")

    def test_user_negative_uid(self):
        with pytest.raises(ValueError):
            SandboxConfig(user="-1:20")

    def test_cpu_must_be_positive(self):
        with pytest.raises(ValueError):
            SandboxConfig(cpu=0)
        with pytest.raises(ValueError):
            SandboxConfig(cpu=-1)

    def test_memory_minimum(self):
        with pytest.raises(ValueError):
            SandboxConfig(memory_mb=32)

    def test_pids_minimum(self):
        with pytest.raises(ValueError):
            SandboxConfig(pids=8)


class TestExtraMountValidation:
    @pytest.mark.parametrize(
        "denied",
        ["/", "/etc", "/var", "/usr", "/bin", "/sbin", "/boot", "/root"],
    )
    def test_system_paths_rejected(self, denied):
        with pytest.raises(ValueError, match="deny-list"):
            SandboxConfig(
                extra_mounts=[ExtraMount(host=denied, container="/x", mode="ro")]
            )

    def test_home_exact_rejected(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))
        with pytest.raises(ValueError, match="deny-list"):
            SandboxConfig(
                extra_mounts=[ExtraMount(host=str(tmp_path), container="/x", mode="ro")]
            )

    def test_workspace_ancestor_rejected(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))
        # ~/blueclaw is an ancestor of ~/blueclaw/workspace
        ancestor = tmp_path / "blueclaw"
        ancestor.mkdir()
        with pytest.raises(ValueError, match="workspace"):
            SandboxConfig(
                extra_mounts=[ExtraMount(host=str(ancestor), container="/x", mode="ro")]
            )

    def test_under_home_allowed(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))
        subdir = tmp_path / "Documents"
        subdir.mkdir()
        cfg = SandboxConfig(
            extra_mounts=[ExtraMount(host=str(subdir), container="/x", mode="ro")]
        )
        assert len(cfg.extra_mounts) == 1

    def test_mount_mode_validation(self):
        with pytest.raises(ValueError):
            ExtraMount(host="/tmp/x", container="/x", mode="rwx")
