"""Tests for blueclaw.server — Agent API Gateway."""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import threading
from unittest.mock import MagicMock, patch

import httpx
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
        patch.dict(os.environ, {"BLUECLAW_API_KEY": ""}),
    ):
        mock_ca.return_value.return_value = mock_agent_result
        mock_btr.return_value = (MagicMock(), MagicMock())
        app = create_server_app(server_config, server_workspace, model=MagicMock())
        yield TestClient(app)


@pytest.fixture
def integration_client(server_config, server_workspace, mock_agent_result):
    with (
        patch("blueclaw.server.create_agent") as mock_ca,
        patch.dict(os.environ, {"BLUECLAW_API_KEY": ""}),
    ):
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


# --- Concurrency / Streaming helpers ---


def _parse_sse(body: str) -> list[tuple[str, dict]]:
    """Parse an SSE response body into [(event_name, data_dict), ...]."""
    events: list[tuple[str, dict]] = []
    for block in body.strip().split("\n\n"):
        if not block.strip():
            continue
        name = ""
        data = ""
        for line in block.splitlines():
            if line.startswith("event:"):
                name = line[7:].strip()
            elif line.startswith("data:"):
                data = line[5:].strip()
        if name:
            events.append((name, json.loads(data) if data else {}))
    return events


# --- Streaming endpoint ---


class TestStreaming:
    def test_stream_emits_delta_and_done(
        self, server_config, server_workspace, mock_agent_result
    ):
        async def fake_stream(_msg):
            yield {"data": "Hello"}
            yield {"data": " world"}
            yield {"result": mock_agent_result}

        agent = MagicMock()
        agent.stream_async = fake_stream
        with (
            patch("blueclaw.server.create_agent", return_value=agent),
            patch("blueclaw.server.cleanup_mcp_clients"),
            patch.object(server_workspace, "write_trace"),
            patch.object(server_workspace, "append_history"),
            patch.dict(os.environ, {"BLUECLAW_API_KEY": ""}),
        ):
            app = create_server_app(server_config, server_workspace, model=MagicMock())
            r = TestClient(app).post("/message/stream", json={"message": "hi"})
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        events = _parse_sse(r.text)
        assert [name for name, _ in events] == ["delta", "delta", "done"]
        assert events[0][1] == {"text": "Hello"}
        assert events[1][1] == {"text": " world"}
        done = events[2][1]
        assert done["reply"] == "The answer is 42."
        assert done["tokens"] == 150
        assert "run_id" in done

    def test_stream_done_has_conversation_id(
        self, server_config, server_workspace, mock_agent_result
    ):
        async def fake_stream(_msg):
            yield {"result": mock_agent_result}

        agent = MagicMock()
        agent.stream_async = fake_stream
        with (
            patch("blueclaw.server.create_agent", return_value=agent),
            patch("blueclaw.server.cleanup_mcp_clients"),
            patch.object(server_workspace, "write_trace"),
            patch.object(server_workspace, "append_history"),
            patch.dict(os.environ, {"BLUECLAW_API_KEY": ""}),
        ):
            app = create_server_app(server_config, server_workspace, model=MagicMock())
            r = TestClient(app).post(
                "/message/stream",
                json={"message": "hi", "conversation_id": "sess-001"},
            )
        events = _parse_sse(r.text)
        done = events[-1][1]
        assert done["conversation_id"] == "sess-001"

    def test_stream_writes_trace_with_api_source(
        self, server_config, server_workspace, mock_agent_result
    ):
        async def fake_stream(_msg):
            yield {"data": "ok"}
            yield {"result": mock_agent_result}

        agent = MagicMock()
        agent.stream_async = fake_stream
        with (
            patch("blueclaw.server.create_agent", return_value=agent),
            patch.dict(os.environ, {"BLUECLAW_API_KEY": ""}),
        ):
            app = create_server_app(server_config, server_workspace, model=MagicMock())
            r = TestClient(app).post("/message/stream", json={"message": "trace me"})
        run_id = _parse_sse(r.text)[-1][1]["run_id"]
        trace = server_workspace.read_trace(run_id)
        assert trace is not None
        assert trace.source == "api"
        assert trace.goal == "trace me"

    def test_stream_missing_auth_returns_401(self, server_config, server_workspace):
        with (
            patch.dict(os.environ, {"BLUECLAW_API_KEY": "secret"}),
            patch("blueclaw.server.create_agent"),
        ):
            app = create_server_app(server_config, server_workspace, model=MagicMock())
            r = TestClient(app).post("/message/stream", json={"message": "hi"})
        assert r.status_code == 401

    def test_stream_correct_bearer_returns_200(
        self, server_config, server_workspace, mock_agent_result
    ):
        async def fake_stream(_msg):
            yield {"result": mock_agent_result}

        agent = MagicMock()
        agent.stream_async = fake_stream
        with (
            patch("blueclaw.server.create_agent", return_value=agent),
            patch("blueclaw.server.cleanup_mcp_clients"),
            patch.object(server_workspace, "write_trace"),
            patch.object(server_workspace, "append_history"),
            patch.dict(os.environ, {"BLUECLAW_API_KEY": "secret"}),
        ):
            app = create_server_app(server_config, server_workspace, model=MagicMock())
            r = TestClient(app).post(
                "/message/stream",
                json={"message": "hi"},
                headers={"Authorization": "Bearer secret"},
            )
        assert r.status_code == 200

    def test_stream_runtime_error_emits_error_event(
        self, server_config, server_workspace
    ):
        async def boom_stream(_msg):
            if False:
                yield {}
            raise RuntimeError("kaboom")

        agent = MagicMock()
        agent.stream_async = boom_stream
        with (
            patch("blueclaw.server.create_agent", return_value=agent),
            patch("blueclaw.server.cleanup_mcp_clients"),
            patch.dict(os.environ, {"BLUECLAW_API_KEY": ""}),
        ):
            app = create_server_app(server_config, server_workspace, model=MagicMock())
            r = TestClient(app).post("/message/stream", json={"message": "hi"})
        events = _parse_sse(r.text)
        assert events[-1][0] == "error"
        assert "kaboom" in events[-1][1]["error"]

    def test_stream_invalid_body_returns_400(self, server_config, server_workspace):
        with (
            patch("blueclaw.server.create_agent"),
            patch.dict(os.environ, {"BLUECLAW_API_KEY": ""}),
        ):
            app = create_server_app(server_config, server_workspace, model=MagicMock())
            r = TestClient(app).post(
                "/message/stream",
                content=b"not json",
                headers={"content-type": "application/json"},
            )
        assert r.status_code == 400


# --- Concurrency cap ---


class TestConcurrencyCap:
    def test_semaphore_caps_simultaneous_runs(
        self, server_workspace, mock_agent_result
    ):
        config = SessionConfig(
            workspace_path=server_workspace.root, max_concurrent_runs=2
        )

        active = 0
        peak = 0
        lock = threading.Lock()
        release = threading.Event()

        def blocking_call(_msg):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            release.wait(timeout=5)
            with lock:
                active -= 1
            return mock_agent_result

        agent = MagicMock(side_effect=blocking_call)

        async def run() -> int:
            with (
                patch("blueclaw.server.create_agent", return_value=agent),
                patch(
                    "blueclaw.server.build_trace_and_record",
                    return_value=(MagicMock(), MagicMock()),
                ),
                patch("blueclaw.server.cleanup_mcp_clients"),
                patch.object(server_workspace, "write_trace"),
                patch.object(server_workspace, "append_history"),
                patch.dict(os.environ, {"BLUECLAW_API_KEY": ""}),
            ):
                app = create_server_app(config, server_workspace, model=MagicMock())
                transport = httpx.ASGITransport(app=app)
                async with httpx.AsyncClient(
                    transport=transport, base_url="http://test"
                ) as client:

                    async def fire():
                        return await client.post(
                            "/message", json={"message": "hi"}, timeout=10
                        )

                    task = asyncio.gather(fire(), fire(), fire(), fire())
                    # Give all 4 a chance to enter the handler
                    await asyncio.sleep(0.3)
                    snapshot = peak
                    release.set()
                    responses = await task
                    assert all(r.status_code == 200 for r in responses)
                    return snapshot

        observed_peak = asyncio.run(run())
        assert observed_peak == 2

    def test_semaphore_shared_across_endpoints(
        self, server_workspace, mock_agent_result
    ):
        """/message and /message/stream draw from the same semaphore."""
        config = SessionConfig(
            workspace_path=server_workspace.root, max_concurrent_runs=1
        )

        active = 0
        peak = 0
        lock = threading.Lock()
        release = threading.Event()

        def blocking_call(_msg):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            release.wait(timeout=5)
            with lock:
                active -= 1
            return mock_agent_result

        async def fake_stream(_msg):
            with lock:
                nonlocal active, peak
                active += 1
                peak = max(peak, active)
            await asyncio.sleep(0.2)
            with lock:
                active -= 1
            yield {"result": mock_agent_result}

        agent = MagicMock(side_effect=blocking_call)
        agent.stream_async = fake_stream

        async def run() -> int:
            with (
                patch("blueclaw.server.create_agent", return_value=agent),
                patch(
                    "blueclaw.server.build_trace_and_record",
                    return_value=(MagicMock(), MagicMock()),
                ),
                patch("blueclaw.server.cleanup_mcp_clients"),
                patch.object(server_workspace, "write_trace"),
                patch.object(server_workspace, "append_history"),
                patch.dict(os.environ, {"BLUECLAW_API_KEY": ""}),
            ):
                app = create_server_app(config, server_workspace, model=MagicMock())
                transport = httpx.ASGITransport(app=app)
                async with httpx.AsyncClient(
                    transport=transport, base_url="http://test"
                ) as client:
                    msg_task = asyncio.create_task(
                        client.post("/message", json={"message": "hi"}, timeout=10)
                    )
                    stream_task = asyncio.create_task(
                        client.post(
                            "/message/stream",
                            json={"message": "hi"},
                            timeout=10,
                        )
                    )
                    await asyncio.sleep(0.3)
                    snapshot = peak
                    release.set()
                    await asyncio.gather(msg_task, stream_task)
                    return snapshot

        observed_peak = asyncio.run(run())
        assert observed_peak == 1


# --- Config validation ---


class TestMaxConcurrentConfig:
    def test_default_is_4(self):
        assert SessionConfig().max_concurrent_runs == 4

    def test_validator_rejects_zero(self):
        with pytest.raises(ValueError):
            SessionConfig(max_concurrent_runs=0)

    def test_validator_rejects_negative(self):
        with pytest.raises(ValueError):
            SessionConfig(max_concurrent_runs=-1)

    def test_load_config_reads_yaml_field(self, tmp_path):
        from blueclaw.session import load_config

        cfg_path = tmp_path / "blueclaw.yaml"
        cfg_path.write_text("server:\n  max_concurrent_runs: 12\n")
        cfg = load_config(cfg_path)
        assert cfg.max_concurrent_runs == 12

    def test_load_config_default_when_omitted(self, tmp_path):
        from blueclaw.session import load_config

        cfg_path = tmp_path / "blueclaw.yaml"
        cfg_path.write_text("model:\n  provider: anthropic\n")
        cfg = load_config(cfg_path)
        assert cfg.max_concurrent_runs == 4


# --- Conversation locking ---


class TestConversationLock:
    def test_lock_for_id_returns_same_lock(self):
        from blueclaw.server import _LockRegistry

        reg = _LockRegistry()

        async def go():
            l1 = await reg.get("a")
            l2 = await reg.get("a")
            assert l1 is l2

        asyncio.run(go())

    def test_lock_for_distinct_ids_differs(self):
        from blueclaw.server import _LockRegistry

        reg = _LockRegistry()

        async def go():
            la = await reg.get("a")
            lb = await reg.get("b")
            assert la is not lb

        asyncio.run(go())

    def test_lock_serializes_same_id(self):
        from blueclaw.server import _LockRegistry

        reg = _LockRegistry()
        events = []

        async def worker(name, hold):
            lock = await reg.get("conv")
            async with lock:
                events.append(("start", name))
                await asyncio.sleep(hold)
                events.append(("end", name))

        async def go():
            await asyncio.gather(worker("A", 0.05), worker("B", 0.01))

        asyncio.run(go())
        # Whichever ran first must fully end before the other starts
        first_end_idx = next(i for i, e in enumerate(events) if e[0] == "end")
        second_start_idx = next(
            i for i, e in enumerate(events) if e[0] == "start" and i > first_end_idx
        )
        assert second_start_idx == first_end_idx + 1
