"""File-upload storage for the HTTP API.

Stores files under workspace/.blueclaw/uploads/<conversation_id>/<file_id>.
Path traversal, oversize, and disallowed-MIME requests raise UploadError.
"""

from __future__ import annotations

import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Callable

MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB
_FILENAME_OK = re.compile(r"[^A-Za-z0-9._-]")
_CID_OK = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_READ_CHUNK = 64 * 1024

_ALLOWED_EXTS: dict[str, str] = {
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".csv": "text/csv",
    ".json": "application/json",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".zip": "application/zip",
}

_MAGIC: dict[str, Callable[[bytes], bool]] = {
    "application/pdf": lambda h: h.startswith(b"%PDF-"),
    "image/png": lambda h: h.startswith(b"\x89PNG\r\n\x1a\n"),
    "image/jpeg": lambda h: h.startswith(b"\xff\xd8\xff"),
    "image/gif": lambda h: h.startswith(b"GIF8"),
    "application/zip": lambda h: h.startswith(b"PK\x03\x04"),
    "image/webp": lambda h: h[:4] == b"RIFF" and h[8:12] == b"WEBP",
}

IMAGE_FORMATS: dict[str, str] = {
    "image/png": "png",
    "image/jpeg": "jpeg",
    "image/gif": "gif",
    "image/webp": "webp",
}


class UploadError(ValueError):
    """Raised when an upload is rejected."""


@dataclass(frozen=True)
class UploadRecord:
    file_id: str
    filename: str
    mime_type: str
    size_bytes: int
    conversation_id: str
    path: Path


def sanitize_filename(name: str) -> str:
    cleaned = _FILENAME_OK.sub("", name)
    cleaned = cleaned.lstrip(".")
    if not cleaned or cleaned in {".", ".."}:
        raise UploadError(f"invalid filename: {name!r}")
    if len(cleaned) > 255:
        cleaned = cleaned[-255:]
    return cleaned


def _validate_cid(cid: str) -> None:
    if not _CID_OK.match(cid):
        raise UploadError(f"invalid conversation_id: {cid!r}")


def _detect_mime(filename: str, head: bytes) -> str:
    ext = Path(filename).suffix.lower()
    if ext not in _ALLOWED_EXTS:
        raise UploadError(f"file type not allowed: {ext or '(no extension)'}")
    expected = _ALLOWED_EXTS[ext]
    check = _MAGIC.get(expected)
    if check and not check(head):
        raise UploadError(
            f"file content does not match extension {ext} (expected {expected})"
        )
    return expected


class UploadStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _conv_dir(self, cid: str) -> Path:
        _validate_cid(cid)
        return self.root / cid

    def save(self, cid: str, filename: str, stream: BinaryIO) -> UploadRecord:
        safe_name = sanitize_filename(filename)
        conv_dir = self._conv_dir(cid)
        conv_dir.mkdir(parents=True, exist_ok=True)
        file_id = f"{uuid.uuid4()}__{safe_name}"
        path = conv_dir / file_id

        head = b""
        size = 0
        try:
            with path.open("wb") as fh:
                while True:
                    chunk = stream.read(_READ_CHUNK)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_UPLOAD_BYTES:
                        raise UploadError(f"file exceeds {MAX_UPLOAD_BYTES} byte cap")
                    if len(head) < 512:
                        head += chunk[: 512 - len(head)]
                    fh.write(chunk)
            mime = _detect_mime(safe_name, head)
        except UploadError:
            path.unlink(missing_ok=True)
            raise
        except OSError:
            path.unlink(missing_ok=True)
            raise

        return UploadRecord(
            file_id=file_id,
            filename=safe_name,
            mime_type=mime,
            size_bytes=size,
            conversation_id=cid,
            path=path,
        )

    def resolve(self, cid: str, file_id: str) -> UploadRecord:
        conv_dir = self._conv_dir(cid).resolve()
        candidate = (conv_dir / file_id).resolve()
        try:
            candidate.relative_to(conv_dir)
        except ValueError as exc:
            raise UploadError(f"file_id escapes conversation dir: {file_id!r}") from exc
        _, _, original = file_id.partition("__")
        if not original:
            raise UploadError(f"malformed file_id: {file_id}")
        ext = Path(original).suffix.lower()
        if ext not in _ALLOWED_EXTS:
            raise UploadError(f"malformed file_id: {file_id} — unrecognized extension")
        mime = _ALLOWED_EXTS[ext]
        if not candidate.is_file():
            raise UploadError(f"file_id not found: {file_id}")
        return UploadRecord(
            file_id=file_id,
            filename=original,
            mime_type=mime,
            size_bytes=candidate.stat().st_size,
            conversation_id=cid,
            path=candidate,
        )

    def purge_conversation(self, cid: str) -> None:
        try:
            _validate_cid(cid)
        except UploadError:
            return
        target = self.root / cid
        if not target.exists():
            return
        shutil.rmtree(target, ignore_errors=True)


@dataclass(frozen=True)
class Attachment:
    """Lightweight attachment record for CLI use (no upload-store backing)."""

    path: Path
    mime_type: str
    size_bytes: int


def format_size(size_bytes: int) -> str:
    size_kb = size_bytes / 1024
    if size_kb >= 1024:
        return f"{size_bytes / 1024 / 1024:.1f} MB"
    return f"{size_kb:.0f} KB"


def build_agent_input(records, user_message: str):
    """Build the agent's prompt argument.

    Returns a plain string when there are no attachments or only non-image
    attachments (path-prefix flow). Returns a list of Strands ContentBlocks
    when one or more image attachments are present, embedding image bytes
    directly so vision-capable models can read pixels.

    Each record needs only `path`, `mime_type`, and `size_bytes` attributes
    — accepts both `UploadRecord` and `Attachment`.
    """
    if not records:
        return user_message

    image_records = [r for r in records if r.mime_type in IMAGE_FORMATS]
    other_records = [r for r in records if r.mime_type not in IMAGE_FORMATS]

    text_lines: list[str] = []
    if other_records:
        text_lines.append(
            "User attached the following files. Read them with the available "
            "tools (shell for text, pdf-mcp for PDFs, etc.):"
        )
        for r in other_records:
            text_lines.append(
                f"  - {r.path}  ({r.mime_type}, {format_size(r.size_bytes)})"
            )
        text_lines.append("")
    text_lines.append(user_message)
    text = "\n".join(text_lines)

    if not image_records:
        return text

    blocks: list[dict] = []
    for r in image_records:
        blocks.append(
            {
                "image": {
                    "format": IMAGE_FORMATS[r.mime_type],
                    "source": {"bytes": r.path.read_bytes()},
                }
            }
        )
    blocks.append({"text": text})
    return blocks


_TRAILING_PUNCT = ".,?!:;)]\""


def _looks_like_path(candidate: str) -> bool:
    """True if a token's tail looks like a file reference rather than a mention.

    Used to surface helpful warnings when a path-shaped `@`-token fails to
    resolve. `@username` and `user@example.com` should NOT trigger warnings.
    """
    if not candidate:
        return False
    if "/" in candidate or candidate.startswith("~"):
        return True
    return Path(candidate).suffix.lower() in _ALLOWED_EXTS


def _try_resolve_attachment(
    token: str, base: Path
) -> tuple[Attachment | None, str | None]:
    """Resolve a single `@<path>` token.

    Returns (attachment, failure_reason). `attachment` is non-None on success;
    on failure, `failure_reason` is non-None only when the token *looked* like
    a file path (so the caller can warn the user). Mention-style `@`-tokens
    return (None, None) and are silently passed through.
    """
    if not token.startswith("@") or len(token) < 2:
        return None, None
    candidate = token[1:]
    candidates = [candidate]
    if candidate and candidate[-1] in _TRAILING_PUNCT:
        candidates.append(candidate[:-1])

    last_reason: str | None = None
    for c in candidates:
        try:
            p = Path(c).expanduser()
            if not p.is_absolute():
                p = base / p
            p_resolved = p.resolve()
        except (OSError, ValueError) as exc:
            last_reason = f"could not resolve path: {exc}"
            continue
        if not p_resolved.exists():
            last_reason = f"file not found: {p_resolved}"
            continue
        if not p_resolved.is_file():
            last_reason = f"not a regular file: {p_resolved}"
            continue
        try:
            with p_resolved.open("rb") as fh:
                head = fh.read(512)
            mime = _detect_mime(p_resolved.name, head)
        except UploadError as exc:
            last_reason = str(exc)
            continue
        except OSError as exc:
            last_reason = f"could not read file: {exc}"
            continue
        return (
            Attachment(
                path=p_resolved,
                mime_type=mime,
                size_bytes=p_resolved.stat().st_size,
            ),
            None,
        )

    if _looks_like_path(candidate):
        return None, last_reason or "could not resolve path"
    return None, None


def parse_at_attachments(
    text: str, base: Path | None = None
) -> tuple[str, list[Attachment], list[tuple[str, str]]]:
    """Scan `text` for whitespace-delimited `@<path>` tokens.

    Returns `(cleaned_text, attachments, failures)`. Failures is a list of
    `(token, reason)` pairs for `@`-tokens that looked like file paths but
    didn't resolve (so the CLI can warn the user). Mention-style `@`-tokens
    (e.g. `@username`, `user@example.com`) are silently left in place.
    """
    if "@" not in text:
        return text, [], []
    if base is None:
        base = Path.cwd()
    out_tokens: list[str] = []
    attachments: list[Attachment] = []
    failures: list[tuple[str, str]] = []
    for token in text.split(" "):
        if not token.startswith("@"):
            out_tokens.append(token)
            continue
        att, reason = _try_resolve_attachment(token, base)
        if att is not None:
            attachments.append(att)
            continue
        if reason is not None:
            failures.append((token, reason))
        out_tokens.append(token)
    cleaned = " ".join(out_tokens).strip()
    return cleaned, attachments, failures
