"""Tests for blueclaw.launcher — host-side sandbox decisions."""

import json
from unittest.mock import MagicMock, patch

import pytest

from blueclaw.launcher import (
    BUILTIN_ENV_ALLOWLIST,
    NetworkValidationError,
    compose_env,
    detect_editable_source,
    validate_network_model,
)
from blueclaw.models import SandboxConfig


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
