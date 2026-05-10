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
from typing import BinaryIO

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

_MAGIC: dict[str, bytes] = {
    "application/pdf": b"%PDF-",
    "image/png": b"\x89PNG\r\n\x1a\n",
    "image/jpeg": b"\xff\xd8\xff",
    "image/gif": b"GIF8",
    "application/zip": b"PK\x03\x04",
    "image/webp": b"RIFF",
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
    magic = _MAGIC.get(expected)
    if magic and not head.startswith(magic):
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
        if not candidate.is_file():
            raise UploadError(f"file_id not found: {file_id}")
        _, _, original = file_id.partition("__")
        if not original:
            raise UploadError(f"malformed file_id: {file_id}")
        mime = _ALLOWED_EXTS.get(
            Path(original).suffix.lower(), "application/octet-stream"
        )
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
