"""Tests for blueclaw.launcher — host-side sandbox decisions."""

import json
from unittest.mock import MagicMock, patch

from blueclaw.launcher import detect_editable_source


class TestDetectEditableSource:
    def test_no_dist_returns_none(self):
        with patch("blueclaw.launcher.importlib.metadata.distribution") as m:
            m.side_effect = Exception("no dist")
            assert detect_editable_source() is None

    def test_no_direct_url_returns_none(self, tmp_path):
        dist = MagicMock()
        dist.locate_file.side_effect = FileNotFoundError
        dist.read_text.return_value = None
        with patch(
            "blueclaw.launcher.importlib.metadata.distribution", return_value=dist
        ):
            assert detect_editable_source() is None

    def test_non_editable_returns_none(self, tmp_path):
        # direct_url.json exists but dir_info.editable is False (or absent)
        dist = MagicMock()
        dist.read_text.return_value = json.dumps(
            {"url": "file:///some/path", "dir_info": {}}
        )
        with patch(
            "blueclaw.launcher.importlib.metadata.distribution", return_value=dist
        ):
            assert detect_editable_source() is None

    def test_editable_returns_path(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        dist = MagicMock()
        dist.read_text.return_value = json.dumps(
            {"url": src.as_uri(), "dir_info": {"editable": True}}
        )
        with patch(
            "blueclaw.launcher.importlib.metadata.distribution", return_value=dist
        ):
            assert detect_editable_source() == src.resolve()

    def test_editable_with_missing_source_dir_returns_none(self, tmp_path):
        dist = MagicMock()
        dist.read_text.return_value = json.dumps(
            {"url": (tmp_path / "gone").as_uri(), "dir_info": {"editable": True}}
        )
        with patch(
            "blueclaw.launcher.importlib.metadata.distribution", return_value=dist
        ):
            assert detect_editable_source() is None
