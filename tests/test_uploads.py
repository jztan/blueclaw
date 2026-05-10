"""Unit tests for blueclaw.uploads.UploadStore."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from blueclaw.uploads import (
    MAX_UPLOAD_BYTES,
    UploadError,
    UploadStore,
    sanitize_filename,
)


@pytest.fixture
def store(tmp_path: Path) -> UploadStore:
    return UploadStore(tmp_path / ".blueclaw" / "uploads")


def test_sanitize_filename_strips_unsafe_chars():
    assert sanitize_filename("../etc/passwd") == "etcpasswd"
    assert sanitize_filename("My Doc.pdf") == "MyDoc.pdf"
    assert sanitize_filename("a/b\\c.txt") == "abc.txt"


def test_sanitize_filename_rejects_empty():
    with pytest.raises(UploadError):
        sanitize_filename("///")
    with pytest.raises(UploadError):
        sanitize_filename("")


def test_save_round_trip(store: UploadStore):
    record = store.save("c-test", "hello.txt", io.BytesIO(b"hello world"))
    assert record.filename == "hello.txt"
    assert record.mime_type == "text/plain"
    assert record.size_bytes == 11
    assert record.path.read_bytes() == b"hello world"
    assert record.file_id.endswith("__hello.txt")


def test_resolve_returns_existing_record(store: UploadStore):
    saved = store.save("c-test", "hello.txt", io.BytesIO(b"hi"))
    resolved = store.resolve("c-test", saved.file_id)
    assert resolved.path == saved.path


def test_resolve_rejects_path_traversal(store: UploadStore):
    store.save("c-test", "hello.txt", io.BytesIO(b"hi"))
    with pytest.raises(UploadError):
        store.resolve("c-test", "../other/hello.txt")
    with pytest.raises(UploadError):
        store.resolve("c-test", "/etc/passwd")


def test_resolve_rejects_unknown_file(store: UploadStore):
    with pytest.raises(UploadError):
        store.resolve("c-test", "nonexistent__hello.txt")


def test_save_rejects_oversize_stream(store: UploadStore):
    big = io.BytesIO(b"x" * (MAX_UPLOAD_BYTES + 1))
    with pytest.raises(UploadError):
        store.save("c-test", "big.bin", big)


def test_save_rejects_disallowed_mime(store: UploadStore):
    with pytest.raises(UploadError):
        store.save("c-test", "evil.exe", io.BytesIO(b"MZ\x90\x00binary"))


def test_save_rejects_bad_cid(store: UploadStore):
    with pytest.raises(UploadError):
        store.save("../escape", "ok.txt", io.BytesIO(b"hi"))


def test_save_rejects_wav_file_named_webp(store: UploadStore):
    """Defends against WEBP/WAV magic-byte collision."""
    wav_header = b"RIFF\x00\x00\x00\x00WAVEfmt "  # not WEBP
    with pytest.raises(UploadError):
        store.save("c-test", "fake.webp", io.BytesIO(wav_header))


def test_save_accepts_real_webp(store: UploadStore):
    webp_header = b"RIFF\x00\x00\x00\x00WEBPVP8 "  # valid WEBP
    record = store.save("c-test", "real.webp", io.BytesIO(webp_header))
    assert record.mime_type == "image/webp"


def test_resolve_rejects_malformed_file_id(store: UploadStore, tmp_path: Path):
    """A file_id without `__` is structurally invalid."""
    # Plant a file directly so existence check would otherwise pass
    cdir = store.root / "c-test"
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "no-separator.txt").write_text("hi")
    with pytest.raises(UploadError, match="malformed"):
        store.resolve("c-test", "no-separator.txt")


def test_resolve_rejects_unknown_extension(store: UploadStore):
    """resolve() should reject file_ids whose extension is outside the allowlist."""
    cdir = store.root / "c-test"
    cdir.mkdir(parents=True, exist_ok=True)
    fid = "deadbeef-1234__notes.weird"
    (cdir / fid).write_text("hi")
    with pytest.raises(UploadError):
        store.resolve("c-test", fid)
