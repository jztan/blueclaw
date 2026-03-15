"""Shell command tool — runs commands within workspace sandbox."""

from __future__ import annotations

import subprocess

from strands import tool

from blueclaw.workspace import Workspace, WorkspaceError

TIMEOUT_SECONDS = 30


def make_shell_command(workspace: Workspace):
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

        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=str(workspace.root),
                capture_output=True,
                text=True,
                timeout=TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            return f"Error: Command timed out after {TIMEOUT_SECONDS}s"

        output = ""
        if result.stdout:
            output += result.stdout
        if result.stderr:
            output += ("\n" if output else "") + result.stderr
        if result.returncode != 0:
            output += f"\n[exit code: {result.returncode}]"
        return output or "(no output)"

    return shell_command
