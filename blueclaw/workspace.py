"""Workspace sandbox enforcement and file operations."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from blueclaw.models import RunRecord, RunTrace

logger = logging.getLogger(__name__)


class WorkspaceError(Exception):
    """Raised when a workspace operation violates sandbox rules."""


# Compiled deny patterns — case-insensitive
DENY_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"\brm\s+(-\w*\s+)*-\w*r\w*\s+(-\w+\s+)*/\s*$", re.IGNORECASE
    ),  # rm -rf /
    re.compile(r"\brm\s+(-\w*\s+)*-\w*r\w*\s+~", re.IGNORECASE),  # rm -rf ~
    re.compile(r"\bdd\b.*\bof\s*=\s*/dev/", re.IGNORECASE),
    re.compile(r"\bmkfs\b", re.IGNORECASE),
    re.compile(r"\bshutdown\b", re.IGNORECASE),
    re.compile(r":\(\)\s*\{", re.IGNORECASE),  # fork bomb
    re.compile(r"\bformat\s+\w:", re.IGNORECASE),
    re.compile(r"\bsudo\b", re.IGNORECASE),  # privilege escalation
    re.compile(r"\bcurl\b.*\|\s*(ba)?sh", re.IGNORECASE),  # curl | bash
    re.compile(r"\bwget\b.*\|\s*(ba)?sh", re.IGNORECASE),  # wget | sh
]


class Workspace:
    """Manages workspace directory with sandbox enforcement."""

    def __init__(self, path: Path) -> None:
        self.root = path
        path.mkdir(parents=True, exist_ok=True)
        (path / ".blueclaw").mkdir(parents=True, exist_ok=True)

    @property
    def context_path(self) -> Path:
        return self.root / "CONTEXT.md"

    @property
    def history_path(self) -> Path:
        return self.root / ".blueclaw" / "history.jsonl"

    @property
    def last_turn_path(self) -> Path:
        return self.root / ".blueclaw" / "last_turn.md"

    def resolve(self, path: str | Path) -> Path:
        """Resolve a relative path inside the workspace."""
        return (self.root / path).resolve()

    def validate_path(self, path: str | Path) -> Path:
        """Validate that a path is inside the workspace. Returns resolved path."""
        p = Path(path)
        if not p.is_absolute():
            resolved = (self.root / p).resolve()
        else:
            resolved = p.resolve()

        # Check symlink targets too
        try:
            real = resolved
            # Walk up path components checking for symlinks
            for parent in [resolved] + list(resolved.parents):
                if parent.is_symlink():
                    real = parent.resolve(strict=False)
                    if not str(real).startswith(str(self.root.resolve())):
                        raise WorkspaceError(
                            f"Symlink escapes workspace: {path} -> {real}"
                        )
        except OSError:
            pass

        if not str(resolved).startswith(str(self.root.resolve())):
            raise WorkspaceError(f"Path outside workspace: {path}")

        return resolved

    # Files managed by blueclaw — blocked from shell access
    PROTECTED_FILES = ("CONTEXT.md", "history.jsonl", "last_turn.md")

    def validate_command(self, command: str) -> None:
        """Check command against destructive deny-list. Raises WorkspaceError."""
        # Block any shell access to blueclaw-managed files
        for pf in self.PROTECTED_FILES:
            if pf in command:
                raise WorkspaceError(
                    f"{pf} is managed by blueclaw — do not access it via shell"
                )

        # Check rm -r on the workspace root itself
        ws_str = str(self.root)
        rm_root_pattern = re.compile(
            rf"\brm\s+(-\w*\s+)*-\w*r\w*\s+{re.escape(ws_str)}\s*$", re.IGNORECASE
        )
        if rm_root_pattern.search(command):
            raise WorkspaceError(f"Cannot remove workspace root: {command}")

        for pattern in DENY_PATTERNS:
            if pattern.search(command):
                raise WorkspaceError(f"Destructive command blocked: {command}")

    def read_context(self) -> str:
        """Read CONTEXT.md. Returns empty string if missing."""
        if self.context_path.exists():
            return self.context_path.read_text()
        return ""

    def write_context(self, content: str) -> None:
        """Write content to CONTEXT.md."""
        self.context_path.write_text(content)

    def read_history(self) -> list[RunRecord]:
        """Read history.jsonl, skipping malformed lines."""
        if not self.history_path.exists():
            return []
        records = []
        for line in self.history_path.read_text().strip().splitlines():
            if not line.strip():
                continue
            try:
                records.append(RunRecord.from_jsonl(line))
            except (ValueError, Exception) as e:
                logger.warning("Skipping malformed history line: %s", e)
        return records

    def append_history(self, record: RunRecord) -> None:
        """Append a RunRecord to history.jsonl."""
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.history_path, "a") as f:
            f.write(record.to_jsonl() + "\n")

    def write_last_turn_checkpoint(self, goal: str, assistant_text: str) -> None:
        """Write crash-recovery checkpoint for latest turn."""
        self.last_turn_path.parent.mkdir(parents=True, exist_ok=True)
        content = (
            "# Last Turn Checkpoint\n\n"
            f"- Goal: {goal}\n"
            "- Assistant:\n\n"
            f"{assistant_text.strip()}\n"
        )
        self.last_turn_path.write_text(content)

    def read_last_turn_checkpoint(self) -> str:
        """Read latest crash-recovery checkpoint."""
        if self.last_turn_path.exists():
            return self.last_turn_path.read_text()
        return ""

    def clear_last_turn_checkpoint(self) -> None:
        """Delete latest crash-recovery checkpoint if present."""
        if self.last_turn_path.exists():
            self.last_turn_path.unlink()

    # --- Trace storage ---

    @property
    def traces_dir(self) -> Path:
        return self.root / ".blueclaw" / "traces"

    def write_trace(self, trace: RunTrace) -> Path:
        """Write a trace file. Returns the path."""
        self.traces_dir.mkdir(parents=True, exist_ok=True)
        path = self.traces_dir / f"{trace.run_id}.json"
        path.write_text(trace.to_json())
        return path

    def read_trace(self, run_id: str) -> RunTrace | None:
        """Read a trace by run_id."""
        path = self.traces_dir / f"{run_id}.json"
        if not path.exists():
            return None
        return RunTrace.from_json(path.read_text())

    def list_traces(self, limit: int = 20) -> list[RunTrace]:
        """List recent traces, newest first."""
        if not self.traces_dir.exists():
            return []
        files = sorted(self.traces_dir.glob("*.json"), reverse=True)
        traces = []
        for f in files[:limit]:
            try:
                traces.append(RunTrace.from_json(f.read_text()))
            except Exception:
                continue
        return traces
