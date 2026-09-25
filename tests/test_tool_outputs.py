"""Tests for durable tool-output storage and retrieval."""

from pathlib import Path

import pytest

from blueclaw.tool_outputs import (
    ArtifactReferenceError,
    ArtifactUnavailableError,
    InvalidSearchQueryError,
    ToolOutputStore,
)


def _store_with_artifact(tmp_path, text):
    capture = tmp_path / ".blueclaw" / "conversations" / "case-a" / "turns" / "turn-001"
    capture.mkdir(parents=True)
    store = ToolOutputStore(tmp_path)
    return store, store.save(capture, text)


def test_save_preserves_exact_text_and_returns_relative_reference(tmp_path):
    capture = tmp_path / ".blueclaw" / "conversations" / "case-a" / "turns" / "turn-001"
    capture.mkdir(parents=True)
    source = "x" * 15_000 + " Needle-42 is the value " + "y" * 15_000

    reference = ToolOutputStore(tmp_path).save(capture, source)

    assert reference.startswith(
        ".blueclaw/conversations/case-a/turns/turn-001/tool-outputs/"
    )
    assert (tmp_path / reference).read_text(encoding="utf-8") == source


def test_save_rejects_workspace_prefix_sibling(tmp_path):
    root = tmp_path / "workspace"
    capture = (
        tmp_path
        / "workspace-evil"
        / ".blueclaw"
        / "conversations"
        / "case-a"
        / "turns"
        / "turn-001"
    )
    capture.mkdir(parents=True)

    with pytest.raises(ArtifactReferenceError):
        ToolOutputStore(root).save(capture, "large output")


def test_save_rejects_symlinked_output_directory(tmp_path):
    capture = tmp_path / ".blueclaw" / "conversations" / "case-a" / "turns" / "turn-001"
    capture.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (capture / "tool-outputs").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ArtifactReferenceError):
        ToolOutputStore(tmp_path).save(capture, "large output")


def test_save_collision_never_overwrites_existing_artifact(tmp_path, monkeypatch):
    capture = tmp_path / ".blueclaw" / "conversations" / "case-a" / "turns" / "turn-001"
    output_dir = capture / "tool-outputs"
    output_dir.mkdir(parents=True)
    existing = output_dir / ("a" * 32 + ".txt")
    existing.write_text("keep this", encoding="utf-8")
    monkeypatch.setattr("blueclaw.tool_outputs.secrets.token_hex", lambda _: "a" * 32)

    with pytest.raises(OSError):
        ToolOutputStore(tmp_path).save(capture, "new output")

    assert existing.read_text(encoding="utf-8") == "keep this"


def test_search_is_case_insensitive_and_returns_reference_provenance(tmp_path):
    store, reference = _store_with_artifact(tmp_path, "before Needle-42 after")

    result = store.search(reference, "needle-42")

    assert "Needle-42" in result
    assert reference in result


def test_search_resolves_artifact_filename_only(tmp_path):
    store, reference = _store_with_artifact(
        tmp_path, "The recovered token is 51436abc84a2edb5."
    )

    result = store.search(Path(reference).name, "51436abc84a2edb5")

    assert "51436abc84a2edb5" in result
    assert reference in result


def test_search_rejects_ambiguous_artifact_filename(tmp_path, monkeypatch):
    store = ToolOutputStore(tmp_path)
    references = []
    monkeypatch.setattr("blueclaw.tool_outputs.secrets.token_hex", lambda _: "a" * 32)
    for conversation_id in ("case-a", "case-b"):
        capture = (
            tmp_path
            / ".blueclaw"
            / "conversations"
            / conversation_id
            / "turns"
            / "turn-001"
        )
        capture.mkdir(parents=True)
        references.append(store.save(capture, "same file name in a different turn"))

    with pytest.raises(ArtifactReferenceError):
        store.search(Path(references[0]).name, "different")


def test_search_treats_regex_metacharacters_literally(tmp_path):
    store, reference = _store_with_artifact(tmp_path, "literal a.*b only")

    result = store.search(reference, "a.*b")

    assert "literal a.*b only" in result
    assert "No matches" not in result


@pytest.mark.parametrize("query", ["", "q" * 257])
def test_search_rejects_empty_or_overlong_query(tmp_path, query):
    store, reference = _store_with_artifact(tmp_path, "content")

    with pytest.raises(InvalidSearchQueryError):
        store.search(reference, query)


def test_search_caps_matches_and_total_response(tmp_path):
    source = "\n".join(
        "x" * 400 + f"row-{index} needle" + "y" * 400 for index in range(20)
    )
    store, reference = _store_with_artifact(tmp_path, source)

    result = store.search(reference, "needle")

    assert result.count("Match ") == 5
    assert "row-0 needle" in result
    assert "row-4 needle" in result
    assert "row-5 needle" not in result
    assert "capped" in result.lower()
    assert len(result) <= 4_000


def test_search_limits_context_around_match(tmp_path):
    store, reference = _store_with_artifact(
        tmp_path, "a" * 500 + "MATCH-HERE" + "b" * 500
    )

    result = store.search(reference, "MATCH-HERE")

    assert "MATCH-HERE" in result
    assert "a" * 161 not in result
    assert "b" * 161 not in result


def test_search_missing_artifact_is_unavailable(tmp_path):
    store, reference = _store_with_artifact(tmp_path, "content")
    (tmp_path / reference).unlink()

    with pytest.raises(ArtifactUnavailableError):
        store.search(reference, "content")


def test_search_rejects_symlinked_output_directory(tmp_path):
    store, reference = _store_with_artifact(tmp_path, "content")
    capture = tmp_path / Path(reference).parents[1]
    output_dir = capture / "tool-outputs"
    moved_dir = capture / "saved-tool-outputs"
    output_dir.rename(moved_dir)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / Path(reference).name).write_text("secret", encoding="utf-8")
    output_dir.symlink_to(outside, target_is_directory=True)

    with pytest.raises(ArtifactReferenceError):
        store.search(reference, "secret")


@pytest.mark.parametrize(
    "reference",
    [
        "../outside.txt",
        "/tmp/outside.txt",
        ".blueclaw/conversations/case-a/turns/turn-001/tool-outputs/not-hex.txt",
    ],
)
def test_search_rejects_invalid_reference(tmp_path, reference):
    store = ToolOutputStore(tmp_path)

    with pytest.raises(ArtifactReferenceError):
        store.search(reference, "query")
