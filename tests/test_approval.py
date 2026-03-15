"""Tests for ApprovalHooks."""

from unittest.mock import MagicMock, patch
from urllib.parse import urlparse
import pytest
from strands.hooks import BeforeToolCallEvent
from blueclaw.models import SessionConfig
from blueclaw.approval import ApprovalHooks


class TestApprovalHooks:
    def test_approval_allowed_domain(self):
        config = SessionConfig(allowlist_domains=["example.com"])
        hooks = ApprovalHooks(config)
        event = MagicMock(spec=BeforeToolCallEvent)
        event.tool_use = {
            "name": "http_request",
            "input": {"url": "https://example.com/foo"},
        }

        hooks.check_domain_allowlist(event)
        # Should not prompt
        with patch("rich.prompt.Confirm.ask") as mock_ask:
            hooks.check_domain_allowlist(event)
            mock_ask.assert_not_called()

    @patch("rich.prompt.Confirm.ask")
    def test_approval_interactive_allow(self, mock_ask):
        config = SessionConfig(allowlist_domains=[])
        hooks = ApprovalHooks(config, scripted=False)
        event = MagicMock(spec=BeforeToolCallEvent)
        event.tool_use = {"name": "http_request", "input": {"url": "https://new.com"}}

        mock_ask.return_value = True
        hooks.check_domain_allowlist(event)

        assert "new.com" in config.allowlist_domains
        mock_ask.assert_called_once()

    @patch("rich.prompt.Confirm.ask")
    def test_approval_interactive_deny(self, mock_ask):
        config = SessionConfig(allowlist_domains=[])
        hooks = ApprovalHooks(config, scripted=False)
        event = MagicMock(spec=BeforeToolCallEvent)
        event.tool_use = {"name": "http_request", "input": {"url": "https://evil.com"}}

        mock_ask.return_value = False
        hooks.check_domain_allowlist(event)

        assert "evil.com" not in config.allowlist_domains
        # Should proceed to tool failure
        mock_ask.assert_called_once()

    def test_approval_scripted_fail(self):
        config = SessionConfig(allowlist_domains=[])
        hooks = ApprovalHooks(config, scripted=True)
        event = MagicMock(spec=BeforeToolCallEvent)
        event.tool_use = {
            "name": "http_request",
            "input": {"url": "https://example.com"},
        }

        # Should NOT prompt — should cancel tool instead
        with patch("rich.prompt.Confirm.ask") as mock_ask:
            hooks.check_domain_allowlist(event)
            mock_ask.assert_not_called()
        assert "not in the allowlist" in event.cancel_tool
        assert "snippets" in event.cancel_tool
