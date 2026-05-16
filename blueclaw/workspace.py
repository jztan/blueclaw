"""Workspace sandbox enforcement and file operations."""

from __future__ import annotations

import logging
import re
import shutil
from datetime import datetime, timedelta, timezone
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
    def soul_path(self) -> Path:
        return self.root / "SOUL.md"

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

    def read_soul(self) -> str:
        """Read SOUL.md (agent identity). Returns empty string if missing."""
        if self.soul_path.exists():
            return self.soul_path.read_text()
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

    def list_traces(
        self, limit: int = 20, since: datetime | None = None
    ) -> list[RunTrace]:
        """List traces, newest first. Optionally filter by start_time >= since."""
        if not self.traces_dir.exists():
            return []
        files = sorted(self.traces_dir.glob("*.json"), reverse=True)
        traces = []
        for f in files:
            if len(traces) >= limit:
                break
            try:
                trace = RunTrace.from_json(f.read_text())
            except Exception:
                continue
            if since is not None and trace.start_time < since:
                continue
            traces.append(trace)
        return traces

    def purge_old_sessions(self, retention_days: int, dry_run: bool = False) -> int:
        """Delete session dirs older than retention_days. Also remove the
        matching uploads/<cid> dir and any orphaned uploads/tmp-* dirs older
        than the same TTL. Returns count of session dirs deleted."""
        sessions_dir = self.root / ".blueclaw" / "sessions"
        uploads_dir = self.root / ".blueclaw" / "uploads"
        if not sessions_dir.exists() and not uploads_dir.exists():
            return 0
        cutoff = datetime.now().timestamp() - retention_days * 86400
        count = 0
        if sessions_dir.exists():
            for session_dir in sessions_dir.iterdir():
                if not session_dir.is_dir():
                    continue
                if session_dir.stat().st_mtime < cutoff:
                    if not dry_run:
                        shutil.rmtree(session_dir, ignore_errors=True)
                        sibling = uploads_dir / session_dir.name
                        if sibling.exists():
                            shutil.rmtree(sibling, ignore_errors=True)
                    count += 1
        if uploads_dir.exists():
            for upload_dir in uploads_dir.iterdir():
                if not upload_dir.is_dir() or not upload_dir.name.startswith("tmp-"):
                    continue
                if upload_dir.stat().st_mtime < cutoff:
                    if not dry_run:
                        shutil.rmtree(upload_dir, ignore_errors=True)
        return count

    def purge_old_traces(self, retention_days: int, dry_run: bool = False) -> int:
        """Delete traces older than retention_days. Returns count."""
        if retention_days <= 0 or not self.traces_dir.exists():
            return 0
        cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).strftime(
            "%Y%m%d"
        )
        count = 0
        for f in self.traces_dir.glob("*.json"):
            date_prefix = f.stem[:8]
            if not date_prefix.isdigit() or len(date_prefix) != 8:
                continue
            if date_prefix < cutoff:
                if not dry_run:
                    f.unlink()
                count += 1
        return count


DEFAULT_CHATS_ROOT = Path.home() / "blueclaw" / "chats"
WORKSPACE_KEY_DEFAULT = "workspace"


def resolve_workspaces(
    *,
    default: Path,
    chat: int | None,
    all_chats: bool,
    chats_root: Path | None = None,
) -> list[tuple[str, "Workspace"]]:
    """Resolve user flags into the list of (key, Workspace) to operate on.

    Read-side helper: never creates chat directories. The Workspace
    constructor mkdirs on init, so existence is checked first and a
    missing --chat target raises FileNotFoundError(chat). Callers map
    that to a friendly error.

    Keys: "workspace" for the default root; "chat:<id>" for per-chat
    roots. In all_chats mode chat dirs are sorted numerically by id.
    """
    root_to_scan = chats_root if chats_root is not None else DEFAULT_CHATS_ROOT
    if chat is not None:
        chat_dir = root_to_scan / str(chat)
        if not chat_dir.exists():
            raise FileNotFoundError(chat)
        return [(f"chat:{chat}", Workspace(chat_dir))]
    if all_chats:
        roots: list[tuple[str, Workspace]] = [
            (WORKSPACE_KEY_DEFAULT, Workspace(default))
        ]
        if root_to_scan.exists():
            numeric_dirs: list[tuple[int, Path]] = []
            for d in root_to_scan.iterdir():
                if not d.is_dir():
                    continue
                try:
                    numeric_dirs.append((int(d.name), d))
                except ValueError:
                    continue
            numeric_dirs.sort(key=lambda nd: nd[0])
            for chat_id, d in numeric_dirs:
                roots.append((f"chat:{chat_id}", Workspace(d)))
        return roots
    return [(WORKSPACE_KEY_DEFAULT, Workspace(default))]
