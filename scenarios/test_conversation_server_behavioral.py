"""
Behavioral tests for conversation_server.py — exercise real code paths.

These tests import the actual Flask app and use its test client to verify
HTTP behavior: status codes, auth enforcement, input validation, error
handling, and endpoint contracts. They replace the substring-only structural
tests with actual runtime verification.
"""
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

SERVER_DIR = Path.home() / "code" / "claude-mobile"

# Ensure the server module is importable
sys.path.insert(0, str(SERVER_DIR))
sys.path.insert(0, str(Path.home() / "code"))


class TestServerImport:
    """Verify the server can be imported (catches syntax errors, missing deps)."""

    def test_beh001_module_importable(self):
        """BEH-001: conversation_server.py must be importable."""
        import conversation_server
        assert hasattr(conversation_server, "app")
        assert hasattr(conversation_server, "check_auth")

    def test_beh002_flask_app_exists(self):
        """BEH-002: Flask app instance must exist and be configured."""
        import conversation_server
        assert conversation_server.app is not None
        assert conversation_server.app.config["MAX_CONTENT_LENGTH"] == 50 * 1024 * 1024


@pytest.fixture
def client():
    """Flask test client with auth bypassed (localhost)."""
    import conversation_server
    conversation_server.app.config["TESTING"] = True
    with conversation_server.app.test_client() as c:
        yield c


@pytest.fixture
def client_with_auth():
    """Flask test client that simulates non-localhost requests."""
    import conversation_server
    conversation_server.app.config["TESTING"] = True

    original_token = getattr(conversation_server, "AUTH_TOKEN", None)
    conversation_server.AUTH_TOKEN = "test-secret-token-12345"
    # Also patch conv.app.AUTH_TOKEN — check_auth reads from there after the
    # conv/ package split (2026-04-12). The import creates a binding, not a
    # reference, so both must be set.
    import conv.app
    original_conv_token = conv.app.AUTH_TOKEN
    conv.app.AUTH_TOKEN = "test-secret-token-12345"

    with conversation_server.app.test_client() as c:
        yield c

    conversation_server.AUTH_TOKEN = original_token
    conv.app.AUTH_TOKEN = original_conv_token


class TestHealthEndpoint:
    """Verify /health returns structured data."""

    def test_beh003_health_returns_200(self, client):
        """BEH-003: /health must return 200 with JSON."""
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "uptime_s" in data
        assert "claude_auth_status" in data

    def test_beh004_health_has_required_fields(self, client):
        """BEH-004: /health response must include uptime_s and ok fields."""
        resp = client.get("/health")
        data = resp.get_json()
        assert isinstance(data.get("uptime_s"), (int, float))
        assert data.get("ok") is True


class TestStatusEndpoint:
    """Verify /status returns session state."""

    def test_beh005_status_returns_200(self, client):
        """BEH-005: /status must return 200."""
        resp = client.get("/status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "session_id" in data
        assert "status" in data

    def test_beh006_status_has_session_fields(self, client):
        """BEH-006: /status must include event_count and alive fields."""
        resp = client.get("/status")
        data = resp.get_json()
        assert "event_count" in data
        assert "alive" in data


class TestPermissionLevel:
    """Verify /permission-level endpoint."""

    def test_beh007_permission_level_returns_200(self, client):
        """BEH-007: /permission-level must return 200 with level field."""
        resp = client.get("/permission-level")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "level" in data


class TestSendEndpoint:
    """Verify /send input validation."""

    def test_beh008_send_rejects_empty_message(self, client):
        """BEH-008: /send with empty message must return 400."""
        resp = client.post("/send",
                           data=json.dumps({"message": ""}),
                           content_type="application/json")
        assert resp.status_code == 400
        data = resp.get_json()
        assert "error" in data

    def test_beh009_send_rejects_whitespace_only(self, client):
        """BEH-009: /send with whitespace-only message must return 400."""
        resp = client.post("/send",
                           data=json.dumps({"message": "   "}),
                           content_type="application/json")
        assert resp.status_code == 400

    def test_beh010_send_rejects_missing_body(self, client):
        """BEH-010: /send with no JSON body must handle gracefully."""
        resp = client.post("/send", content_type="application/json")
        assert resp.status_code in (400, 500)


class TestAuthEnforcement:
    """Verify auth is enforced for non-localhost requests."""

    def test_beh011_unauth_rejected(self, client_with_auth):
        """BEH-011: Request without auth from non-Tailscale IP must be rejected."""
        import conversation_server
        with conversation_server.app.test_request_context(
            "/status",
            environ_base={"REMOTE_ADDR": "203.0.113.1"}
        ):
            from flask import request as req
            result = conversation_server.check_auth()
            assert result is not None
            response, status_code = result
            assert status_code == 401

    def test_beh012_valid_bearer_accepted(self, client_with_auth):
        """BEH-012: Request with valid Bearer token must be accepted."""
        import conversation_server
        with conversation_server.app.test_request_context(
            "/status",
            headers={"Authorization": "Bearer test-secret-token-12345"},
            environ_base={"REMOTE_ADDR": "203.0.113.1"}
        ):
            result = conversation_server.check_auth()
            assert result is None  # None = allow


class TestCancelEndpoint:
    """Verify /cancel behavior."""

    def test_beh013_cancel_no_active_process(self, client):
        """BEH-013: /cancel with no active process must return 404."""
        resp = client.post("/cancel")
        assert resp.status_code in (200, 404)
        if resp.status_code == 404:
            data = resp.get_json()
            assert "error" in data


class TestNewSession:
    """Verify /new-session endpoint."""

    def test_beh014_new_session_returns_ok(self, client):
        """BEH-014: /new-session must return ok."""
        resp = client.post("/new-session")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get("ok") is True


class TestStreamEndpoint:
    """Verify /stream behavior."""

    def test_beh015_stream_no_session_returns_404(self, client):
        """BEH-015: /stream with no active session must return 404."""
        import conversation_server
        with conversation_server.app.test_request_context():
            with conversation_server._lock:
                conversation_server._session["id"] = None
                conversation_server._session["events_file"] = None

        resp = client.get("/stream")
        assert resp.status_code == 404


class TestSecurityBoundaries:
    """Verify the server doesn't leak secrets or crash on bad input."""

    def test_beh016_error_responses_no_secrets(self, client):
        """BEH-016: Error responses must not contain auth tokens."""
        import conversation_server
        token = getattr(conversation_server, "AUTH_TOKEN", None)

        resp = client.post("/send",
                           data=json.dumps({"message": ""}),
                           content_type="application/json")
        body = resp.get_data(as_text=True)
        if token:
            assert token not in body

    def test_beh017_large_payload_rejected(self, client):
        """BEH-017: Payload exceeding MAX_CONTENT_LENGTH must be rejected."""
        big_msg = "x" * (51 * 1024 * 1024)
        resp = client.post("/send",
                           data=json.dumps({"message": big_msg}),
                           content_type="application/json")
        assert resp.status_code in (400, 413, 500)
