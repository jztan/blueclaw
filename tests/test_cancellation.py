"""Cancellation must preserve the first stop reason and block later work."""

import shlex
import os
import signal
import sys
import threading
import time
from pathlib import Path
from unittest.mock import Mock
from unittest.mock import patch

from rich.console import Console
from io import StringIO

from blueclaw.cancellation import CancellationControl
from blueclaw.models import SessionConfig
from blueclaw.observer import ObserverHooks
from blueclaw.runner import runner_session
from blueclaw.session import load_tools
from blueclaw.tools.shell import make_shell_command
from blueclaw.workspace import Workspace


def test_first_stop_reason_and_late_attachment():
    control = CancellationControl()
    agent = Mock()
    assert control.request_stop("user")
    assert control.request_stop("disconnect")
    control.attach(agent)
    agent.cancel.assert_called_once_with()
    assert control.reason == "user"
    assert control.begin_finalization() == "user"
    assert not control.request_stop("timeout")


def test_stop_before_spawn_does_not_write(tmp_path):
    control = CancellationControl()
    control.request_stop("user")
    shell = make_shell_command(Workspace(tmp_path), cancellation=control)
    reply = shell(command="echo changed > must-not-exist")
    assert "cancel" in reply.lower()
    assert not (tmp_path / "must-not-exist").exists()


def test_stop_kills_owned_command_before_late_side_effect(tmp_path):
    control = CancellationControl()
    shell = make_shell_command(Workspace(tmp_path), cancellation=control)
    script = tmp_path / "slow.py"
    script.write_text(
        "from pathlib import Path\n"
        "import time\n"
        "Path('ready').write_text('yes')\n"
        "time.sleep(10)\n"
        "Path('late-side-effect').write_text('bad')\n"
    )
    command = f"{shlex.quote(sys.executable)} {shlex.quote(str(script))}"
    result = []
    thread = threading.Thread(target=lambda: result.append(shell(command=command)))
    thread.start()
    try:
        deadline = time.monotonic() + 3
        while not (tmp_path / "ready").exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert (tmp_path / "ready").exists()
        assert control.request_stop("user")
        thread.join(timeout=3)
        assert not thread.is_alive()
        assert "cancel" in result[0].lower()
        assert not (tmp_path / "late-side-effect").exists()
    finally:
        control.request_stop("user")
        thread.join(timeout=11)


def test_load_tools_passes_control_to_shell(tmp_path):
    control = CancellationControl()
    config = SessionConfig(tools=["shell"], workspace_path=tmp_path)
    shell = load_tools(config, Workspace(tmp_path), cancellation=control)[0]
    control.request_stop("user")
    reply = shell(command="echo changed > must-not-exist")
    assert "cancel" in reply.lower()
    assert not (tmp_path / "must-not-exist").exists()


def test_runner_session_attaches_control(tmp_path, monkeypatch):
    agent = Mock()
    agent.messages = []
    created = []

    def factory(**kwargs):
        created.append(kwargs)
        return agent

    monkeypatch.setattr("blueclaw.runner.create_agent", factory)
    monkeypatch.setattr("blueclaw.runner.cleanup_mcp_clients", lambda observer: None)
    control = CancellationControl()
    config = SessionConfig(tools=[], workspace_path=tmp_path)
    with runner_session(
        config, Workspace(tmp_path), model=Mock(), cancellation=control
    ) as ctx:
        assert ctx.cancellation is control
        control.request_stop("user")
    agent.cancel.assert_called_once_with()
    assert created[0]["cancellation"] is control


def test_observer_rejects_tool_after_stop():
    control = CancellationControl()
    observer = ObserverHooks(Console(file=StringIO()), cancellation=control)
    control.request_stop("user")
    event = Mock()
    event.tool_use = {"toolUseId": "t", "name": "shell_command", "input": {}}
    observer.before_tool(event)
    assert event.cancel_tool
    assert observer.trace_steps == []


def test_stop_kills_descendant_after_shell_exits(tmp_path):
    control = CancellationControl()
    shell = make_shell_command(Workspace(tmp_path), cancellation=control)
    script = tmp_path / "child.py"
    script.write_text(
        "from pathlib import Path\n"
        "import os, signal, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "Path('child-pid').write_text(str(os.getpid()))\n"
        "time.sleep(10)\n"
        "Path('late-side-effect').write_text('bad')\n"
    )
    command = f"{shlex.quote(sys.executable)} {shlex.quote(str(script))} &"
    result = []
    thread = threading.Thread(target=lambda: result.append(shell(command=command)))
    thread.start()
    pid = None
    try:
        deadline = time.monotonic() + 3
        while not (tmp_path / "child-pid").exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert (tmp_path / "child-pid").exists()
        pid = int((tmp_path / "child-pid").read_text())
        control.request_stop("user")
        thread.join(timeout=3)
        assert not thread.is_alive()
        assert "cancel" in result[0].lower()
        assert not (tmp_path / "late-side-effect").exists()
        deadline = time.monotonic() + 3
        while _pid_running(pid) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not _pid_running(pid)
    finally:
        control.request_stop("user")
        thread.join(timeout=11)
        if pid is not None and _pid_running(pid):
            os.kill(pid, signal.SIGKILL)


def _pid_running(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    if sys.platform.startswith("linux"):
        try:
            # An orphaned child may remain as a zombie until PID 1 reaps it.
            state = Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1][0]
        except FileNotFoundError:
            return False
        return state not in {"Z", "X"}
    return True


def test_large_stdout_and_stderr_are_drained(tmp_path):
    shell = make_shell_command(Workspace(tmp_path))
    script = (
        "import sys; "
        "sys.stdout.write('A' * 100000); "
        "sys.stderr.write('B' * 100000)"
    )
    command = f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}"
    result = shell(command=command)
    assert result.count("A") == 100000
    assert result.count("B") == 100000


def test_stop_racing_with_spawn_is_observed(tmp_path):
    control = CancellationControl()
    shell = make_shell_command(Workspace(tmp_path), cancellation=control)
    entered_spawn = threading.Event()
    allow_spawn = threading.Event()
    real_popen = __import__("subprocess").Popen
    result = []

    def paused_popen(*args, **kwargs):
        entered_spawn.set()
        assert allow_spawn.wait(timeout=2)
        return real_popen(*args, **kwargs)

    command = "sleep 10"
    with patch("blueclaw.cancellation.subprocess.Popen", side_effect=paused_popen):
        thread = threading.Thread(target=lambda: result.append(shell(command=command)))
        thread.start()
        try:
            assert entered_spawn.wait(timeout=2)
            stopper = threading.Thread(target=lambda: control.request_stop("user"))
            stopper.start()
            allow_spawn.set()
            stopper.join(timeout=2)
            thread.join(timeout=3)
            assert not stopper.is_alive()
            assert not thread.is_alive()
            assert "cancel" in result[0].lower()
        finally:
            allow_spawn.set()
            control.request_stop("user")
            thread.join(timeout=11)
