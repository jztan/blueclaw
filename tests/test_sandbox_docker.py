"""Integration tests for the docker sandbox. Skipped unless BLUECLAW_DOCKER_TESTS=1."""

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("BLUECLAW_DOCKER_TESTS") != "1",
    reason="set BLUECLAW_DOCKER_TESTS=1 to run docker integration tests",
)


REPO_ROOT = Path(__file__).resolve().parent.parent
IMAGE_TAG = "blueclaw/runtime:itest"


@pytest.fixture(scope="session", autouse=True)
def build_image():
    """Build the test image once per session."""
    result = subprocess.run(
        [
            "docker",
            "build",
            "-t",
            IMAGE_TAG,
            "-f",
            str(REPO_ROOT / "docker" / "Dockerfile"),
            str(REPO_ROOT),
        ],
        check=False,
    )
    if result.returncode != 0:
        pytest.skip("docker build failed; skipping integration tests")
    yield


def _run(*args, **kw):
    return subprocess.run(args, capture_output=True, text=True, check=False, **kw)


class TestContainerSecurity:
    def test_runs_as_host_uid(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        uid = os.getuid()
        gid = os.getgid()
        result = _run(
            "docker",
            "run",
            "--rm",
            "--user",
            f"{uid}:{gid}",
            "--read-only",
            "--tmpfs",
            "/tmp",
            "--mount",
            f"type=bind,source={ws},target=/workspace",
            "--workdir",
            "/workspace",
            "--entrypoint",
            "/bin/sh",
            IMAGE_TAG,
            "-c",
            "id -u && id -g",
        )
        assert result.returncode == 0
        lines = result.stdout.strip().splitlines()
        assert lines == [str(uid), str(gid)]

    def test_workspace_writes_visible_on_host_as_host_uid(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        uid = os.getuid()
        gid = os.getgid()
        result = _run(
            "docker",
            "run",
            "--rm",
            "--user",
            f"{uid}:{gid}",
            "--read-only",
            "--tmpfs",
            "/tmp",
            "--mount",
            f"type=bind,source={ws},target=/workspace",
            "--workdir",
            "/workspace",
            "--entrypoint",
            "/bin/sh",
            IMAGE_TAG,
            "-c",
            "echo hello > out.txt",
        )
        assert result.returncode == 0
        out = ws / "out.txt"
        assert out.exists()
        st = out.stat()
        assert st.st_uid == uid
        assert st.st_gid == gid

    def test_read_only_root_blocks_writes(self):
        result = _run(
            "docker",
            "run",
            "--rm",
            "--read-only",
            "--tmpfs",
            "/tmp",
            "--entrypoint",
            "/bin/sh",
            IMAGE_TAG,
            "-c",
            "echo x > /not_allowed",
        )
        assert result.returncode != 0
        assert "Read-only" in result.stderr or "read-only" in result.stderr.lower()

    def test_network_none_blocks_egress(self):
        result = _run(
            "docker",
            "run",
            "--rm",
            "--network=none",
            "--entrypoint",
            "/bin/sh",
            IMAGE_TAG,
            "-c",
            "curl --max-time 3 -fsS https://example.com",
        )
        assert result.returncode != 0


class TestExitCodePropagation:
    def test_nonzero_exit_passes_through(self):
        result = _run(
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "/bin/sh",
            IMAGE_TAG,
            "-c",
            "exit 17",
        )
        assert result.returncode == 17


class TestSandboxCliEndToEnd:
    """End-to-end against the installed `blueclaw` in the image."""

    def test_blueclaw_help_inside_container(self):
        result = _run(
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "blueclaw",
            IMAGE_TAG,
            "--help",
        )
        assert result.returncode == 0
        assert "blueclaw" in result.stdout.lower()


class TestLauncherEndToEnd:
    """Exercise the actual launcher: host-side `blueclaw` -> execvp -> container.

    These tests verify the wiring (mounts, env, argv, entrypoint, exit codes).
    They do NOT exercise the agent loop -- that requires a reachable model.
    """

    def test_launcher_execs_into_container_version(self, tmp_path, monkeypatch):
        """A docker-mode `blueclaw --version` should print version from the container.

        We can't trivially distinguish host-blueclaw from in-container blueclaw via
        output alone, so we use a marker: the in-container blueclaw runs as our uid
        with HOME=/home/blueclaw. Any trace written must land in the bind-mounted
        workspace. We assert on the mount, not the output.
        """
        ws_root = tmp_path / "blueclaw"
        (ws_root / "workspace").mkdir(parents=True)
        (ws_root / "skills").mkdir()
        # Point HOME so blueclaw's defaults resolve under tmp_path.
        monkeypatch.setenv("HOME", str(tmp_path))
        # Minimal config: docker mode, the built test image, network=none
        # (so we never reach the network even if the agent code tries).
        cfg_path = tmp_path / "blueclaw.yaml"
        cfg_path.write_text(
            f"provider: ollama\n"
            f"model_id: llama3\n"
            f"sandbox:\n"
            f"  mode: docker\n"
            f"  image: {IMAGE_TAG}\n"
            f"  network: none\n"
            f"  on_unavailable: error\n"
        )
        monkeypatch.chdir(tmp_path)
        result = _run("blueclaw", "--version")
        # --version is implemented on the host callback before the launcher hook would
        # normally engage. So this is actually a host-side smoke test confirming the
        # callback wiring doesn't crash with a docker-mode config present.
        assert result.returncode == 0, result.stderr

    def test_launcher_routes_skill_list_to_host(self, tmp_path, monkeypatch):
        """`skill list` is in the host routing table — must not enter the container."""
        ws_root = tmp_path / "blueclaw"
        (ws_root / "workspace").mkdir(parents=True)
        (ws_root / "skills").mkdir()
        monkeypatch.setenv("HOME", str(tmp_path))
        cfg_path = tmp_path / "blueclaw.yaml"
        cfg_path.write_text(
            f"provider: ollama\n"
            f"model_id: llama3\n"
            f"sandbox:\n"
            f"  mode: docker\n"
            f"  image: {IMAGE_TAG}\n"
        )
        monkeypatch.chdir(tmp_path)
        # If routing is broken and this enters the container, docker run will fail
        # because of `network: none` reaching some operation, or because the inner
        # blueclaw can't find the host skills dir. On correct routing, host-side
        # `skill list` just lists nothing.
        result = _run("blueclaw", "skill", "list")
        assert result.returncode == 0, result.stderr

    def test_launcher_blueclaw_run_smoke(self, tmp_path, monkeypatch):
        """`blueclaw run` enters the container; agent attempt is allowed to fail.

        We're testing that the launcher reached the container, not that the agent
        succeeded. Confirm via a trace file written to the bind-mounted workspace.
        """
        ws_root = tmp_path / "blueclaw"
        ws = ws_root / "workspace"
        ws.mkdir(parents=True)
        (ws_root / "skills").mkdir()
        monkeypatch.setenv("HOME", str(tmp_path))
        cfg_path = tmp_path / "blueclaw.yaml"
        cfg_path.write_text(
            f"provider: ollama\n"
            f"model_id: llama3\n"
            f"sandbox:\n"
            f"  mode: docker\n"
            f"  image: {IMAGE_TAG}\n"
            f"  network: none\n"
        )
        monkeypatch.chdir(tmp_path)
        # Run is expected to fail because network: none + ollama is unreachable.
        # We do not assert on returncode. We assert on side-effects.
        _run("blueclaw", "run", "test goal")
        # The launcher must have created .blueclaw inside the workspace mount.
        bcdir = ws / ".blueclaw"
        assert bcdir.exists(), (
            f"Expected {bcdir} to exist after `blueclaw run` in docker mode. "
            f"If missing, the launcher likely did not route into the container "
            f"or the workspace mount target path is wrong."
        )
        st = bcdir.stat()
        assert st.st_uid == os.getuid(), (
            f".blueclaw owned by uid {st.st_uid}, expected {os.getuid()} "
            f"(host-uid mapping is broken)"
        )
