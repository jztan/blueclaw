"""Storage and retrieval helpers for full tool output artifacts."""

from __future__ import annotations

import os
import re
import secrets
import tempfile
from pathlib import Path, PurePosixPath


class ArtifactReferenceError(ValueError):
    """Raised when an artifact path or reference is outside the allowed shape."""


class ArtifactUnavailableError(FileNotFoundError):
    """Raised when a referenced artifact is missing or cannot be read."""


class InvalidSearchQueryError(ValueError):
    """Raised when a retrieval query is empty or exceeds its limit."""


MAX_QUERY_CHARS = 256
MAX_MATCHES = 5
SNIPPET_CONTEXT_CHARS = 160
MAX_RESPONSE_CHARS = 4_000
_ARTIFACT_ID_RE = re.compile(r"^[0-9a-f]{32}\.txt$")
_ARTIFACT_REF_MARKER_RE = re.compile(r"\[blueclaw artifact: ([^\]\r\n]+)\]")
_TURN_RE = re.compile(r"^turn-([0-9]{3,})$")
_MAX_SESSION_ID_LEN = 128
_FORBIDDEN_SESSION_ID_CHARS = ("/", "\\", "\x00")
_MAX_ID_ATTEMPTS = 3


def _valid_session_component(value: str) -> bool:
    if not value or len(value) > _MAX_SESSION_ID_LEN or value in (".", ".."):
        return False
    if any(ch in value for ch in _FORBIDDEN_SESSION_ID_CHARS):
        return False
    return not any(ch.isspace() or ord(ch) < 32 for ch in value)


class ToolOutputStore:
    """Persist and search output artifacts inside turn capture directories."""

    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = Path(workspace_root).resolve()

    def _capture_relative_path(self, capture_path: Path) -> Path:
        candidate = Path(capture_path)
        if not candidate.is_absolute():
            candidate = self.workspace_root / candidate
        candidate = Path(os.path.abspath(candidate))

        try:
            relative = candidate.relative_to(self.workspace_root)
        except ValueError as exc:
            raise ArtifactReferenceError("invalid artifact capture path") from exc

        if len(relative.parts) != 5:
            raise ArtifactReferenceError("invalid artifact capture path")
        if relative.parts[:2] != (".blueclaw", "conversations"):
            raise ArtifactReferenceError("invalid artifact capture path")
        if not _valid_session_component(relative.parts[2]):
            raise ArtifactReferenceError("invalid artifact capture path")
        if relative.parts[3] != "turns":
            raise ArtifactReferenceError("invalid artifact capture path")
        turn_match = _TURN_RE.fullmatch(relative.parts[4])
        if turn_match is None or int(turn_match.group(1)) == 0:
            raise ArtifactReferenceError("invalid artifact capture path")

        current = self.workspace_root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise ArtifactReferenceError("invalid artifact capture path")

        try:
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise ArtifactReferenceError("invalid artifact capture path") from exc
        if not resolved.is_relative_to(self.workspace_root) or not resolved.is_dir():
            raise ArtifactReferenceError("invalid artifact capture path")
        return relative

    def save(self, capture_path: Path, text: str) -> str:
        """Atomically save text under a validated capture and return its ref."""
        relative_capture = self._capture_relative_path(capture_path)
        capture = self.workspace_root / relative_capture
        output_dir = capture / "tool-outputs"
        if output_dir.is_symlink():
            raise ArtifactReferenceError("invalid artifact output directory")
        output_dir.mkdir(exist_ok=True)
        if output_dir.is_symlink() or not output_dir.resolve().is_relative_to(
            capture.resolve()
        ):
            raise ArtifactReferenceError("invalid artifact output directory")

        fd, temp_name = tempfile.mkstemp(prefix=".output-", dir=output_dir)
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "wb") as output:
                output.write(text.encode("utf-8"))
                output.flush()

            for _ in range(_MAX_ID_ATTEMPTS):
                artifact_id = secrets.token_hex(16)
                target = output_dir / f"{artifact_id}.txt"
                try:
                    os.link(temp_path, target, follow_symlinks=False)
                except FileExistsError:
                    continue
                reference = (relative_capture / "tool-outputs" / target.name).as_posix()
                return reference
            raise FileExistsError("could not allocate a unique tool-output artifact ID")
        finally:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass

    @staticmethod
    def _reference_parts(artifact_ref: str) -> tuple[str, ...]:
        if not isinstance(artifact_ref, str) or not artifact_ref:
            raise ArtifactReferenceError("invalid artifact reference")
        if "\\" in artifact_ref or "\x00" in artifact_ref:
            raise ArtifactReferenceError("invalid artifact reference")
        reference_path = PurePosixPath(artifact_ref)
        parts = reference_path.parts
        if (
            reference_path.is_absolute()
            or len(parts) != 7
            or artifact_ref != "/".join(parts)
        ):
            raise ArtifactReferenceError("invalid artifact reference")
        if parts[:2] != (".blueclaw", "conversations"):
            raise ArtifactReferenceError("invalid artifact reference")
        if not _valid_session_component(parts[2]):
            raise ArtifactReferenceError("invalid artifact reference")
        if parts[3] != "turns" or parts[5] != "tool-outputs":
            raise ArtifactReferenceError("invalid artifact reference")
        turn_match = _TURN_RE.fullmatch(parts[4])
        if turn_match is None or int(turn_match.group(1)) == 0:
            raise ArtifactReferenceError("invalid artifact reference")
        if _ARTIFACT_ID_RE.fullmatch(parts[6]) is None:
            raise ArtifactReferenceError("invalid artifact reference")
        return parts

    def _artifact_path(self, artifact_ref: str) -> Path:
        parts = self._reference_parts(artifact_ref)
        current = self.workspace_root
        for part in parts:
            current = current / part
            if current.is_symlink():
                raise ArtifactReferenceError("invalid artifact reference")

        try:
            resolved = current.resolve(strict=True)
        except FileNotFoundError as exc:
            raise ArtifactUnavailableError("artifact is unavailable") from exc
        except OSError as exc:
            raise ArtifactUnavailableError("artifact is unavailable") from exc
        if not resolved.is_relative_to(self.workspace_root):
            raise ArtifactReferenceError("invalid artifact reference")
        if not resolved.is_file():
            raise ArtifactUnavailableError("artifact is unavailable")
        return resolved

    def _canonical_reference(self, artifact_ref: str) -> str:
        try:
            self._reference_parts(artifact_ref)
            return artifact_ref
        except ArtifactReferenceError:
            if (
                not isinstance(artifact_ref, str)
                or _ARTIFACT_ID_RE.fullmatch(artifact_ref) is None
            ):
                raise

        pattern = f".blueclaw/conversations/*/turns/turn-*/tool-outputs/{artifact_ref}"
        matches = []
        for candidate in self.workspace_root.glob(pattern):
            reference = candidate.relative_to(self.workspace_root).as_posix()
            try:
                self._artifact_path(reference)
            except (ArtifactReferenceError, ArtifactUnavailableError):
                continue
            matches.append(reference)

        if not matches:
            raise ArtifactUnavailableError("artifact is unavailable")
        if len(matches) != 1:
            raise ArtifactReferenceError("ambiguous artifact reference")
        return matches[0]

    def search(self, artifact_ref: str, query: str) -> str:
        """Return bounded literal-search snippets with their source reference."""
        if not isinstance(query, str) or not query.strip():
            raise InvalidSearchQueryError("query must not be empty")
        if len(query) > MAX_QUERY_CHARS:
            raise InvalidSearchQueryError(f"query exceeds {MAX_QUERY_CHARS} characters")

        artifact_ref = self._canonical_reference(artifact_ref)
        artifact_path = self._artifact_path(artifact_ref)
        try:
            text = artifact_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise ArtifactUnavailableError("artifact is unavailable") from exc

        matcher = re.compile(re.escape(query), re.IGNORECASE)
        matches = []
        for match in matcher.finditer(text):
            matches.append(match)
            if len(matches) > MAX_MATCHES:
                break

        if not matches:
            return f"Artifact: {artifact_ref}\nNo matches for {query!r}."

        snippets = []
        for index, match in enumerate(matches[:MAX_MATCHES], start=1):
            start = max(0, match.start() - SNIPPET_CONTEXT_CHARS)
            end = min(len(text), match.end() + SNIPPET_CONTEXT_CHARS)
            snippet = text[start:end]
            if start:
                snippet = "…" + snippet
            if end < len(text):
                snippet += "…"
            snippets.append(f"Match {index}: {snippet}")

        response = f"Artifact: {artifact_ref}\n" + "\n".join(snippets)
        if len(matches) > MAX_MATCHES:
            response += f"\nResults capped at {MAX_MATCHES} matches."
        return response[:MAX_RESPONSE_CHARS]


def extract_artifact_refs(text: str) -> list[str]:
    """Extract valid artifact-reference markers in first-seen order."""
    references = []
    seen = set()
    for match in _ARTIFACT_REF_MARKER_RE.finditer(text):
        reference = match.group(1)
        try:
            ToolOutputStore._reference_parts(reference)
        except ArtifactReferenceError:
            continue
        if reference not in seen:
            seen.add(reference)
            references.append(reference)
    return references
