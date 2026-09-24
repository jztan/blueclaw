"""Shell command tool — runs commands within workspace sandbox."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

from blueclaw.cancellation import CancellationControl

from strands import tool

from blueclaw.workspace import Workspace, WorkspaceError

TIMEOUT_SECONDS = 30


def make_shell_command(
    workspace: Workspace, cancellation: CancellationControl | None = None
):
    """Factory that returns a shell_command tool bound to a workspace."""

    @tool
    def shell_command(command: str) -> str:
        """Run a shell command in the workspace directory.

        The command runs with the workspace as working directory.
        Destructive commands (rm -rf /, sudo, etc.) are blocked.
        Output is captured and returned as a string.
        """
        try:
            workspace.validate_command(command)
        except WorkspaceError as e:
            return f"Error: {e}"

        control = cancellation or CancellationControl()
        process = control.spawn(command, Path(workspace.root))
        if process is None:
            return "Error: Command cancelled"

        deadline = time.monotonic() + TIMEOUT_SECONDS
        timed_out = False
        stopped = False
        try:
            while True:
                if control.event.is_set():
                    stopped = True
                    break
                if time.monotonic() >= deadline:
                    timed_out = True
                    break
                try:
                    stdout, stderr = process.communicate(timeout=0.1)
                    break
                except subprocess.TimeoutExpired:
                    continue
            if stopped or timed_out:
                _terminate_group(process)
                stdout, stderr = process.communicate()
        finally:
            control.release(process)
            for pipe in (process.stdout, process.stderr):
                if pipe is not None:
                    pipe.close()

        if stopped:
            return _combined_output(stdout, stderr) + "\n[command cancelled]"
        if timed_out:
            return f"Error: Command timed out after {TIMEOUT_SECONDS}s"

        output = _combined_output(stdout, stderr)
        if process.returncode != 0:
            output += f"\n[exit code: {process.returncode}]"
        return output or "(no output)"

    return shell_command


def _combined_output(stdout: str, stderr: str) -> str:
    return stdout + ("\n" if stdout and stderr else "") + stderr


def _signal_group(process: subprocess.Popen, sig: int) -> None:
    try:
        os.killpg(process.pid, sig)
    except ProcessLookupError:
        pass


def _terminate_group(process: subprocess.Popen) -> None:
    _signal_group(process, signal.SIGTERM)
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        pass
    # The shell may exit before a descendant. Escalate against its group anyway.
    _signal_group(process, signal.SIGKILL)
