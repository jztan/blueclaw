"""Strands tool for bounded search of saved tool-output artifacts."""

from __future__ import annotations

from strands import tool

from blueclaw.tool_outputs import (
    ArtifactReferenceError,
    InvalidSearchQueryError,
    ToolOutputStore,
)
from blueclaw.workspace import Workspace


def make_retrieve_output(workspace: Workspace):
    """Create a retrieval tool limited to artifacts in ``workspace``."""
    store = ToolOutputStore(workspace.root)

    @tool
    def retrieve_tool_output(artifact_ref: str, query: str) -> str:
        """Search a saved large tool result for a literal string.

        Use the artifact reference or its filename shown in a truncated tool
        result. Returns bounded matching snippets and the canonical reference
        for follow-up searches.
        """
        try:
            return store.search(artifact_ref, query)
        except ArtifactReferenceError:
            return "Error: invalid artifact reference."
        except InvalidSearchQueryError:
            return "Error: invalid query. Use 1 to 256 characters."
        except OSError:
            return "Artifact unavailable."

    return retrieve_tool_output
