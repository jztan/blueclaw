"""Host-side sandbox decisions: editable detect, env compose, docker argv, execvp."""

from __future__ import annotations

import importlib.metadata
import json
from pathlib import Path
from urllib.parse import urlparse


def detect_editable_source() -> Path | None:
    """Return the source path if blueclaw is installed editable (PEP 660), else None.

    Reads dist-info/direct_url.json per PEP 610. An editable install is signaled
    by `dir_info.editable == True`. The url is `file://...` pointing at the source.
    """
    try:
        dist = importlib.metadata.distribution("blueclaw")
    except Exception:
        return None
    try:
        raw = dist.read_text("direct_url.json")
    except Exception:
        raw = None
    if not raw:
        return None
    try:
        meta = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not meta.get("dir_info", {}).get("editable"):
        return None
    url = meta.get("url")
    if not url or not url.startswith("file://"):
        return None
    src = Path(urlparse(url).path).resolve()
    if not src.exists():
        return None
    return src
