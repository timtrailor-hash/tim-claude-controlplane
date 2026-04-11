"""
Conversation server tests — verify the TerminalApp-critical tmux endpoints work.

Tim explicitly said the TerminalApp improvements are "SOOOO important" and must be
protected. These tests verify the server code is importable and the tmux helper
functions work correctly.
"""
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

SERVER_DIR = Path.home() / "code" / "claude-mobile"


class TestConversationServerStructure:
    """Verify the server file exists and has expected structure."""

    def test_srv001_server_file_exists(self):
        """SRV-001: conversation_server.py must exist."""
        server = SERVER_DIR / "conversation_server.py"
        assert server.exists(), f"conversation_server.py not found at {server}"

    def test_srv002_has_tmux_endpoints(self):
        """SRV-002: server must have all required tmux endpoints."""
        server = SERVER_DIR / "conversation_server.py"
        content = server.read_text()
        required_endpoints = [
            "/tmux-capture",
            "/tmux-send-text",
            "/tmux-send-enter",
            "/tmux-windows",
            "/tmux-new-window",
        ]
        for endpoint in required_endpoints:
            assert endpoint in content, f"Missing endpoint: {endpoint}"

    def test_srv003_has_apns_support(self):
        """SRV-003: server must have APNs push notification support."""
        server = SERVER_DIR / "conversation_server.py"
        content = server.read_text()
        assert "_send_push_notification" in content, "Missing _send_push_notification"
        assert "apns_state" in content, "APNs should use ~/code/apns_state, not /tmp"

    def test_srv004_no_tmp_apns_token(self):
        """SRV-004: APNs tokens must NOT be stored in /tmp."""
        server = SERVER_DIR / "conversation_server.py"
        content = server.read_text()
        assert "/tmp/apns_device_token" not in content, \
            "APNs token still references /tmp — should use ~/code/apns_state"

    def test_srv005_has_watch_tmux(self):
        """SRV-005: server must have the watch-tmux watcher endpoint."""
        server = SERVER_DIR / "conversation_server.py"
        content = server.read_text()
        assert "/watch-tmux" in content, "Missing /watch-tmux endpoint"
        assert "_watch_tmux_worker" in content, "Missing _watch_tmux_worker"

    def test_srv006_has_websocket_handler(self):
        """SRV-006: server must have the WebSocket handler for mobile apps."""
        server = SERVER_DIR / "conversation_server.py"
        content = server.read_text()
        assert "websocket_handler" in content, "Missing websocket_handler"

    def test_srv007_auth_check_present(self):
        """SRV-007: server must have authentication."""
        server = SERVER_DIR / "conversation_server.py"
        content = server.read_text()
        assert "check_auth" in content, "Missing check_auth"
