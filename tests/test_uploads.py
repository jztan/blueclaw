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


def test_message_request_accepts_file_ids():
    from blueclaw.models import MessageRequest

    req = MessageRequest(message="hi", file_ids=["a__x.txt", "b__y.pdf"])
    assert req.file_ids == ["a__x.txt", "b__y.pdf"]


def test_message_request_defaults_file_ids_empty():
    from blueclaw.models import MessageRequest

    req = MessageRequest(message="hi")
    assert req.file_ids == []


def test_upload_response_shape():
    from blueclaw.models import UploadResponse

    resp = UploadResponse(
        file_id="abc__hi.txt",
        filename="hi.txt",
        mime_type="text/plain",
        size_bytes=2,
        conversation_id="c-1",
    )
    assert resp.file_id == "abc__hi.txt"
    assert resp.size_bytes == 2


# --- parse_at_attachments + build_agent_input ---


def _png_bytes() -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
        b"\x1f\x15\xc4\x89"
        b"\x00\x00\x00\rIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01"
        b"\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )


def test_parse_at_attachments_resolves_image(tmp_path: Path):
    from blueclaw.uploads import parse_at_attachments

    img = tmp_path / "pic.png"
    img.write_bytes(_png_bytes())
    cleaned, atts, failed = parse_at_attachments(f"What is in @{img} please?")
    assert len(atts) == 1
    assert atts[0].path == img.resolve()
    assert atts[0].mime_type == "image/png"
    assert failed == []
    assert "What is in" in cleaned and "please?" in cleaned
    assert str(img) not in cleaned


def test_parse_at_attachments_strips_trailing_punctuation(tmp_path: Path):
    from blueclaw.uploads import parse_at_attachments

    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    cleaned, atts, failed = parse_at_attachments(f"Read @{pdf}, then summarize.")
    assert len(atts) == 1
    assert atts[0].mime_type == "application/pdf"
    assert failed == []
    assert "Read" in cleaned and "summarize." in cleaned


def test_parse_at_attachments_leaves_mention_tokens_silently():
    """`@username` and `user@example.com` must not produce warnings."""
    from blueclaw.uploads import parse_at_attachments

    cleaned, atts, failed = parse_at_attachments(
        "ping @username and email me at user@example.com"
    )
    assert atts == []
    assert failed == []
    assert "@username" in cleaned
    assert "user@example.com" in cleaned


def test_parse_at_attachments_warns_on_path_typo(tmp_path: Path):
    """An `@`-token that looks like a path but doesn't resolve is reported."""
    from blueclaw.uploads import parse_at_attachments

    bogus = tmp_path / "does_not_exist.png"
    cleaned, atts, failed = parse_at_attachments(
        f"can you see @{bogus}", base=tmp_path
    )
    assert atts == []
    assert len(failed) == 1
    token, reason = failed[0]
    assert token == f"@{bogus}"
    assert "not found" in reason.lower()
    # The token still passes through to the message text
    assert str(bogus) in cleaned


def test_parse_at_attachments_resolves_relative_path(tmp_path: Path):
    from blueclaw.uploads import parse_at_attachments

    img = tmp_path / "pic.png"
    img.write_bytes(_png_bytes())
    cleaned, atts, failed = parse_at_attachments(
        "describe @pic.png", base=tmp_path
    )
    assert len(atts) == 1
    assert atts[0].path == img.resolve()
    assert failed == []
    assert cleaned == "describe"


def test_parse_at_attachments_auto_detects_bare_absolute_path(tmp_path: Path):
    """A bare /abs/path token is auto-attached without an @-prefix."""
    from blueclaw.uploads import parse_at_attachments

    img = tmp_path / "pic.png"
    img.write_bytes(_png_bytes())
    cleaned, atts, failed = parse_at_attachments(f"{img} read this please")
    assert len(atts) == 1
    assert atts[0].path == img.resolve()
    assert atts[0].mime_type == "image/png"
    assert failed == []
    assert "read this please" in cleaned
    assert str(img) not in cleaned


def test_parse_at_attachments_auto_detects_single_quoted_path(tmp_path: Path):
    """Shift+drag pastes that single-quote the absolute path are recognized."""
    from blueclaw.uploads import parse_at_attachments

    img = tmp_path / "pic.png"
    img.write_bytes(_png_bytes())
    cleaned, atts, failed = parse_at_attachments(f"'{img}' read this")
    assert len(atts) == 1
    assert atts[0].path == img.resolve()
    assert failed == []
    assert "read this" in cleaned


def test_parse_at_attachments_auto_detects_double_quoted_path(tmp_path: Path):
    from blueclaw.uploads import parse_at_attachments

    img = tmp_path / "pic.png"
    img.write_bytes(_png_bytes())
    cleaned, atts, failed = parse_at_attachments(f'"{img}" hi')
    assert len(atts) == 1
    assert atts[0].path == img.resolve()
    assert "hi" in cleaned


def test_parse_at_attachments_does_not_auto_detect_relative(tmp_path: Path):
    """Bare `pic.png` (no @-prefix, no leading /) must NOT auto-attach."""
    from blueclaw.uploads import parse_at_attachments

    img = tmp_path / "pic.png"
    img.write_bytes(_png_bytes())
    # Even though the file exists at base=tmp_path, a bare relative token
    # should pass through. Auto-detection is reserved for absolute paths.
    cleaned, atts, failed = parse_at_attachments("look at pic.png", base=tmp_path)
    assert atts == []
    assert failed == []
    assert "pic.png" in cleaned


def test_build_agent_input_with_image_returns_blocks(tmp_path: Path):
    from blueclaw.uploads import Attachment, build_agent_input

    img = tmp_path / "pic.png"
    img.write_bytes(_png_bytes())
    att = Attachment(
        path=img, mime_type="image/png", size_bytes=img.stat().st_size
    )
    out = build_agent_input([att], "what is this?")
    assert isinstance(out, list)
    image_blocks = [b for b in out if "image" in b]
    text_blocks = [b for b in out if "text" in b]
    assert len(image_blocks) == 1
    assert image_blocks[0]["image"]["format"] == "png"
    assert image_blocks[0]["image"]["source"]["bytes"] == _png_bytes()
    assert text_blocks[0]["text"] == "what is this?"


def test_build_agent_input_with_pdf_only_returns_string(tmp_path: Path):
    from blueclaw.uploads import Attachment, build_agent_input

    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    att = Attachment(path=pdf, mime_type="application/pdf", size_bytes=10)
    out = build_agent_input([att], "summarize")
    assert isinstance(out, str)
    assert "User attached the following files" in out
    assert "summarize" in out


def test_build_agent_input_no_attachments_returns_string():
    from blueclaw.uploads import build_agent_input

    assert build_agent_input([], "just a question") == "just a question"


def test_build_agent_input_rejects_oversize_image(tmp_path: Path):
    """Inline images larger than the cap raise UploadError so we don't ship
    a payload Anthropic will reject with 400."""
    from blueclaw.uploads import (
        MAX_INLINE_IMAGE_BYTES,
        Attachment,
        UploadError,
        build_agent_input,
    )

    big_img = tmp_path / "big.png"
    big_img.write_bytes(_png_bytes())  # actual content doesn't matter
    att = Attachment(
        path=big_img,
        mime_type="image/png",
        size_bytes=MAX_INLINE_IMAGE_BYTES + 1,
    )
    with pytest.raises(UploadError, match="image too large"):
        build_agent_input([att], "describe")
