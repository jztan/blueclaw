"""Tests for blueclaw.server — Agent API Gateway."""

from __future__ import annotations

import asyncio
import inspect
import os
from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient

import blueclaw.server
from blueclaw.models import SessionConfig
from blueclaw.server import create_server_app
from blueclaw.web import create_app
from blueclaw.workspace import Workspace, WorkspaceError

# --- Fixtures ---


@pytest.fixture
def server_config(tmp_path):
    return SessionConfig(workspace_path=tmp_path / "workspace")


@pytest.fixture
def server_workspace(server_config):
    return Workspace(server_config.workspace_path)


@pytest.fixture
def client(server_config, server_workspace, mock_agent_result):
    with (
        patch("blueclaw.server.create_agent") as mock_ca,
        patch("blueclaw.server.build_trace_and_record") as mock_btr,
        patch("blueclaw.server.cleanup_mcp_clients"),
        patch.object(server_workspace, "write_trace"),
        patch.object(server_workspace, "append_history"),
    ):
        mock_ca.return_value.return_value = mock_agent_result
        mock_btr.return_value = (MagicMock(), MagicMock())
        app = create_server_app(server_config, server_workspace, model=MagicMock())
        yield TestClient(app)


@pytest.fixture
def integration_client(server_config, server_workspace, mock_agent_result):
    with patch("blueclaw.server.create_agent") as mock_ca:
        mock_ca.return_value.return_value = mock_agent_result
        app = create_server_app(server_config, server_workspace, model=MagicMock())
        yield TestClient(app)


# --- Health ---


class TestHealth:
    def test_health_returns_200(self, client):
        r = client.get("/health")
        assert r.status_code == 200

    def test_health_body(self, client):
        data = client.get("/health").json()
        assert data["status"] == "ok"
        assert "version" in data

    def test_health_no_auth_required(self, client):
        with patch.dict(os.environ, {"BLUECLAW_API_KEY": "secret"}):
            r = client.get("/health")
        assert r.status_code == 200


# --- Message Success ---


class TestMessageSuccess:
    def test_post_message_200(self, client):
        r = client.post("/message", json={"message": "hello"})
        assert r.status_code == 200

    def test_reply_is_string(self, client):
        data = client.post("/message", json={"message": "hello"}).json()
        assert isinstance(data["reply"], str)

    def test_run_id_format(self, client):
        import re

        data = client.post("/message", json={"message": "hello"}).json()
        assert re.match(r"^\d{8}-\d{6}-[0-9a-f]{4}$", data["run_id"])

    def test_tokens_is_int(self, client):
        data = client.post("/message", json={"message": "hello"}).json()
        assert isinstance(data["tokens"], int)

    def test_conversation_id_null_when_not_provided(self, client):
        data = client.post("/message", json={"message": "hello"}).json()
        assert data["conversation_id"] is None

    def test_conversation_id_echoed(self, client):
        data = client.post(
            "/message", json={"message": "hello", "conversation_id": "sess-001"}
        ).json()
        assert data["conversation_id"] == "sess-001"


# --- Request Parsing ---


class TestRequestParsing:
    def test_missing_message_field_400(self, client):
        r = client.post("/message", json={"other": "value"})
        assert r.status_code == 400

    def test_invalid_json_400(self, client):
        r = client.post(
            "/message",
            content=b"not json",
            headers={"content-type": "application/json"},
        )
        assert r.status_code == 400

    def test_invalid_conversation_id_400(self, client):
        r = client.post(
            "/message",
            json={"message": "hi", "conversation_id": "../evil"},
        )
        assert r.status_code == 400

    def test_body_at_limit_not_413(self, client):
        # Exactly 1 MB JSON — json.dumps({"message": "x"*N}) has 15-byte overhead
        msg = "x" * (1_048_576 - 15)
        r = client.post("/message", json={"message": msg})
        assert r.status_code != 413

    def test_body_over_limit_413(self, client):
        big = "x" * 1_048_577
        r = client.post(
            "/message",
            content=big.encode(),
            headers={"content-type": "application/json"},
        )
        assert r.status_code == 413


# --- Authentication ---


class TestAuthentication:
    def test_no_key_configured_allows_request(self, client):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("BLUECLAW_API_KEY", None)
            r = client.post("/message", json={"message": "hi"})
        assert r.status_code == 200

    def test_correct_bearer_token_200(self, client):
        with patch.dict(os.environ, {"BLUECLAW_API_KEY": "secret"}):
            r = client.post(
                "/message",
                json={"message": "hi"},
                headers={"Authorization": "Bearer secret"},
            )
        assert r.status_code == 200

    def test_wrong_token_401(self, client):
        with patch.dict(os.environ, {"BLUECLAW_API_KEY": "secret"}):
            r = client.post(
                "/message",
                json={"message": "hi"},
                headers={"Authorization": "Bearer wrong"},
            )
        assert r.status_code == 401

    def test_no_header_401(self, client):
        with patch.dict(os.environ, {"BLUECLAW_API_KEY": "secret"}):
            r = client.post("/message", json={"message": "hi"})
        assert r.status_code == 401

    def test_malformed_header_401(self, client):
        with patch.dict(os.environ, {"BLUECLAW_API_KEY": "secret"}):
            r = client.post(
                "/message",
                json={"message": "hi"},
                headers={"Authorization": "Token secret"},
            )
        assert r.status_code == 401

    def test_auth_uses_timing_safe_comparison(self):
        assert "hmac.compare_digest" in inspect.getsource(blueclaw.server)


# --- Error Paths ---


class TestErrorPaths:
    def test_timeout_returns_504(self, client):
        with patch(
            "blueclaw.server.asyncio.wait_for",
            side_effect=asyncio.TimeoutError(),
        ):
            r = client.post("/message", json={"message": "hi"})
        assert r.status_code == 504

    def test_workspace_error_returns_500(self, client):
        with patch(
            "blueclaw.server.create_agent",
            side_effect=WorkspaceError("boom"),
        ):
            r = client.post("/message", json={"message": "hi"})
        assert r.status_code == 500
        assert "workspace" in r.json()["error"]

    def test_runtime_error_returns_500(self, client):
        with patch(
            "blueclaw.server.create_agent",
            side_effect=RuntimeError("boom"),
        ):
            r = client.post("/message", json={"message": "hi"})
        assert r.status_code == 500


# --- Trace Integration ---


class TestTraceIntegration:
    def test_trace_written_on_success(self, integration_client, server_workspace):
        r = integration_client.post("/message", json={"message": "hello trace"})
        assert r.status_code == 200
        run_id = r.json()["run_id"]
        trace = server_workspace.read_trace(run_id)
        assert trace is not None
        assert trace.source == "api"
        assert trace.goal == "hello trace"

    def test_trace_visible_in_web_api(self, integration_client, server_workspace):
        r = integration_client.post("/message", json={"message": "visible test"})
        run_id = r.json()["run_id"]
        web_client = TestClient(create_app(server_workspace))
        r2 = web_client.get(f"/api/traces/{run_id}")
        assert r2.status_code == 200


# --- CORS ---


class TestCors:
    def test_cors_localhost(self, client):
        r = client.options(
            "/message",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert "access-control-allow-origin" in r.headers

    def test_cors_127(self, client):
        r = client.options(
            "/message",
            headers={
                "Origin": "http://127.0.0.1:8080",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert "access-control-allow-origin" in r.headers


# --- Context.md not modified ---


class TestContextMdNotModified:
    def test_context_md_not_modified(self, client, server_workspace):
        server_workspace.write_context("original content")
        client.post("/message", json={"message": "hello"})
        assert server_workspace.read_context() == "original content"
