"""Hooks for user approval of sensitive actions (e.g., domain allowlist)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from rich.prompt import Confirm
from strands.hooks import BeforeToolCallEvent, HookProvider, HookRegistry

if TYPE_CHECKING:
    from blueclaw.models import SessionConfig


class ApprovalHooks(HookProvider):
    """Enforce domain allowlist with interactive approval."""

    def __init__(self, config: SessionConfig, scripted: bool = False) -> None:
        self.config = config
        self.scripted = scripted

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(BeforeToolCallEvent, self.check_domain_allowlist)

    def check_domain_allowlist(self, event: BeforeToolCallEvent) -> None:
        """Check allowlist for network tools. Prompt user if domain is new."""
        tool_name = event.tool_use["name"]

        # Identify network tools (e.g., http_request)
        if tool_name == "http_request":
            url = event.tool_use.get("input", {}).get("url")
            if not url:
                return

            domain = urlparse(url).hostname
            if not domain:
                return

            if domain in self.config.allowlist_domains:
                return

            # Domain not in allowlist
            if self.scripted:
                # Scripted mode: do nothing, let tool fail with error message
                pass
            else:
                # Interactive mode: prompt user
                msg = f"Tool '{tool_name}' wants to access '{domain}'. Allow?"
                if Confirm.ask(msg):
                    self.config.allowlist_domains.append(domain)
                # If denied, do nothing -> tool will run and fail
