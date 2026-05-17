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
from blueclaw.models import RunRecord, RunTrace, SessionConfig
from blueclaw.server import create_server_app
from blueclaw.web import create_app
from blueclaw.workspace import Workspace, WorkspaceError

# --- Test helpers ---


def _fake_build_trace_and_record(
    result,
    goal,
    observer,
    config,
    run_id,
    start_time,
    end_time,
    source="terminal",
    conversation_id=None,
):
    """Stand-in for session.build_trace_and_record that produces real models
    using the run_id minted by runner.finalize. Used wherever tests previously
    patched build_trace_and_record to return (MagicMock(), MagicMock()) —
    the migrated handle_message reads outcome.trace.run_id, which needs to
    be a real string."""
    trace = RunTrace(
        run_id=run_id,
        goal=goal,
        start_time=start_time,
        end_time=end_time,
        model_id=config.model_id,
        steps=[],
        total_tokens=150,
        total_cost=None,
        status="success",
        source=source,
        conversation_id=conversation_id,
    )
    record = RunRecord(
        ts=end_time,
        goal=goal,
        tools=[],
        tokens=150,
        cost=None,
        conversation_id=conversation_id,
    )
    return trace, record


# --- Fixtures ---


@pytest.fixture
def server_config(tmp_path):
    return SessionConfig(workspace_path=tmp_path / "workspace")


@pytest.fixture
def server_workspace(server_config):
    return Workspace(server_config.workspace_path)


@pytest.fixture
def client(server_config, server_workspace, mock_agent_result):
    # Both /message and /message/stream now go through runner_session +
    # finalize — patch at blueclaw.runner.* (where they resolve their
    # imports). Streaming tests that need agent.stream_async build their
    # own apps with the same pattern.
    with (
        patch("blueclaw.runner.create_agent") as mock_ca,
        patch(
            "blueclaw.runner.build_trace_and_record",
            side_effect=_fake_build_trace_and_record,
        ),
        patch("blueclaw.runner.cleanup_mcp_clients"),
        patch.object(server_workspace, "write_trace"),
        patch.object(server_workspace, "append_history"),
        patch.dict(os.environ, {"BLUECLAW_API_KEY": ""}),
    ):
        mock_ca.return_value.return_value = mock_agent_result
        app = create_server_app(server_config, server_workspace, model=MagicMock())
        yield TestClient(app)


@pytest.fixture
def integration_client(server_config, server_workspace, mock_agent_result):
    with (
        patch("blueclaw.runner.create_agent") as mock_ca,
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


class TestPlayground:
    def test_playground_returns_html(self, client):
        r = client.get("/playground")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/html")
        assert "blueclaw playground" in r.text.lower()

    def test_playground_no_auth_required(self, client):
        with patch.dict(os.environ, {"BLUECLAW_API_KEY": "secret"}):
            r = client.get("/playground")
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
            "blueclaw.runner.create_agent",
            side_effect=WorkspaceError("boom"),
        ):
            r = client.post("/message", json={"message": "hi"})
        assert r.status_code == 500
        assert "workspace" in r.json()["error"]

    def test_runtime_error_returns_500(self, client):
        with patch(
            "blueclaw.runner.create_agent",
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
            patch("blueclaw.runner.create_agent", return_value=agent),
            patch(
                "blueclaw.runner.build_trace_and_record",
                side_effect=_fake_build_trace_and_record,
            ),
            patch("blueclaw.runner.cleanup_mcp_clients"),
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
            patch("blueclaw.runner.create_agent", return_value=agent),
            patch(
                "blueclaw.runner.build_trace_and_record",
                side_effect=_fake_build_trace_and_record,
            ),
            patch("blueclaw.runner.cleanup_mcp_clients"),
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
            patch("blueclaw.runner.create_agent", return_value=agent),
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
            patch("blueclaw.runner.create_agent"),
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
            patch("blueclaw.runner.create_agent", return_value=agent),
            patch(
                "blueclaw.runner.build_trace_and_record",
                side_effect=_fake_build_trace_and_record,
            ),
            patch("blueclaw.runner.cleanup_mcp_clients"),
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
            patch("blueclaw.runner.create_agent", return_value=agent),
            patch(
                "blueclaw.runner.build_trace_and_record",
                side_effect=_fake_build_trace_and_record,
            ),
            patch("blueclaw.runner.cleanup_mcp_clients"),
            patch.dict(os.environ, {"BLUECLAW_API_KEY": ""}),
        ):
            app = create_server_app(server_config, server_workspace, model=MagicMock())
            r = TestClient(app).post("/message/stream", json={"message": "hi"})
        events = _parse_sse(r.text)
        assert events[-1][0] == "error"
        assert "kaboom" in events[-1][1]["error"]

    def test_stream_invalid_body_returns_400(self, server_config, server_workspace):
        with (
            patch("blueclaw.runner.create_agent"),
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
                patch("blueclaw.runner.create_agent", return_value=agent),
                patch(
                    "blueclaw.runner.build_trace_and_record",
                    side_effect=_fake_build_trace_and_record,
                ),
                patch("blueclaw.runner.cleanup_mcp_clients"),
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
                # Both endpoints now resolve via blueclaw.runner — single
                # patch layer covers /message and /message/stream.
                patch("blueclaw.runner.create_agent", return_value=agent),
                patch(
                    "blueclaw.runner.build_trace_and_record",
                    side_effect=_fake_build_trace_and_record,
                ),
                patch("blueclaw.runner.cleanup_mcp_clients"),
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


class TestStatefulMessage:
    def test_session_manager_passed_when_conversation_id_set(
        self, server_config, server_workspace, mock_agent_result
    ):
        with (
            patch("blueclaw.runner.create_agent") as mock_ca,
            patch(
                "blueclaw.runner.build_trace_and_record",
                side_effect=_fake_build_trace_and_record,
            ),
            patch("blueclaw.runner.cleanup_mcp_clients"),
            patch.object(server_workspace, "write_trace"),
            patch.object(server_workspace, "append_history"),
            patch.dict(os.environ, {"BLUECLAW_API_KEY": ""}),
            patch("blueclaw.server.FileSessionManager") as mock_fsm,
        ):
            mock_ca.return_value.return_value = mock_agent_result
            app = create_server_app(server_config, server_workspace, model=MagicMock())
            with TestClient(app) as tc:
                r = tc.post("/message", json={"message": "hi", "conversation_id": "c1"})
            assert r.status_code == 200
            mock_fsm.assert_called_once()
            kwargs = mock_fsm.call_args.kwargs
            assert kwargs["session_id"] == "c1"
            expected_dir = str(server_workspace.root / ".blueclaw" / "sessions")
            assert kwargs["storage_dir"] == expected_dir
            ca_kwargs = mock_ca.call_args.kwargs
            assert ca_kwargs.get("session_manager") is mock_fsm.return_value

    def test_no_session_manager_when_conversation_id_absent(
        self, server_config, server_workspace, mock_agent_result
    ):
        with (
            patch("blueclaw.runner.create_agent") as mock_ca,
            patch(
                "blueclaw.runner.build_trace_and_record",
                side_effect=_fake_build_trace_and_record,
            ),
            patch("blueclaw.runner.cleanup_mcp_clients"),
            patch.object(server_workspace, "write_trace"),
            patch.object(server_workspace, "append_history"),
            patch.dict(os.environ, {"BLUECLAW_API_KEY": ""}),
            patch("blueclaw.server.FileSessionManager") as mock_fsm,
        ):
            mock_ca.return_value.return_value = mock_agent_result
            app = create_server_app(server_config, server_workspace, model=MagicMock())
            with TestClient(app) as tc:
                r = tc.post("/message", json={"message": "hi"})
            assert r.status_code == 200
            mock_fsm.assert_not_called()
            ca_kwargs = mock_ca.call_args.kwargs
            assert ca_kwargs.get("session_manager") is None

    def test_conversation_id_threaded_into_trace_builder(
        self, server_config, server_workspace, mock_agent_result
    ):
        with (
            patch("blueclaw.runner.create_agent") as mock_ca,
            patch(
                "blueclaw.runner.build_trace_and_record",
                side_effect=_fake_build_trace_and_record,
            ) as mock_btr,
            patch("blueclaw.runner.cleanup_mcp_clients"),
            patch.object(server_workspace, "write_trace"),
            patch.object(server_workspace, "append_history"),
            patch.dict(os.environ, {"BLUECLAW_API_KEY": ""}),
            patch("blueclaw.server.FileSessionManager"),
        ):
            mock_ca.return_value.return_value = mock_agent_result
            app = create_server_app(server_config, server_workspace, model=MagicMock())
            with TestClient(app) as tc:
                tc.post("/message", json={"message": "hi", "conversation_id": "cX"})
            assert mock_btr.call_args.kwargs["conversation_id"] == "cX"


class TestStatefulConcurrency:
    """Project does not use pytest-asyncio. Follow the existing pattern in
    tests/test_server.py (TestSemaphore around line 478): sync test method,
    `asyncio.run(run())` inside, all patches set up inline within `run()`."""

    def test_same_id_requests_are_serialized(
        self, server_config, server_workspace, mock_agent_result
    ):
        def slow_agent(_msg):
            import time

            time.sleep(0.15)
            return mock_agent_result

        agent = MagicMock(side_effect=slow_agent)

        async def run():
            with (
                patch("blueclaw.runner.create_agent", return_value=agent),
                patch(
                    "blueclaw.runner.build_trace_and_record",
                    side_effect=_fake_build_trace_and_record,
                ),
                patch("blueclaw.runner.cleanup_mcp_clients"),
                patch.object(server_workspace, "write_trace"),
                patch.object(server_workspace, "append_history"),
                patch.dict(os.environ, {"BLUECLAW_API_KEY": ""}),
                patch("blueclaw.server.FileSessionManager"),
            ):
                app = create_server_app(
                    server_config, server_workspace, model=MagicMock()
                )
                transport = httpx.ASGITransport(app=app)
                async with httpx.AsyncClient(
                    transport=transport, base_url="http://test"
                ) as ac:
                    start = asyncio.get_event_loop().time()
                    responses = await asyncio.gather(
                        ac.post(
                            "/message",
                            json={"message": "x", "conversation_id": "s"},
                            timeout=10,
                        ),
                        ac.post(
                            "/message",
                            json={"message": "y", "conversation_id": "s"},
                            timeout=10,
                        ),
                    )
                    elapsed = asyncio.get_event_loop().time() - start
                    assert all(r.status_code == 200 for r in responses)
                    return elapsed

        elapsed = asyncio.run(run())
        assert elapsed >= 0.30, f"expected >=0.30 s, got {elapsed:.3f}"

    def test_distinct_ids_run_in_parallel(
        self, server_config, server_workspace, mock_agent_result
    ):
        def slow_agent(_msg):
            import time

            time.sleep(0.15)
            return mock_agent_result

        agent = MagicMock(side_effect=slow_agent)

        async def run():
            with (
                patch("blueclaw.runner.create_agent", return_value=agent),
                patch(
                    "blueclaw.runner.build_trace_and_record",
                    side_effect=_fake_build_trace_and_record,
                ),
                patch("blueclaw.runner.cleanup_mcp_clients"),
                patch.object(server_workspace, "write_trace"),
                patch.object(server_workspace, "append_history"),
                patch.dict(os.environ, {"BLUECLAW_API_KEY": ""}),
                patch("blueclaw.server.FileSessionManager"),
            ):
                app = create_server_app(
                    server_config, server_workspace, model=MagicMock()
                )
                transport = httpx.ASGITransport(app=app)
                async with httpx.AsyncClient(
                    transport=transport, base_url="http://test"
                ) as ac:
                    start = asyncio.get_event_loop().time()
                    responses = await asyncio.gather(
                        ac.post(
                            "/message",
                            json={"message": "x", "conversation_id": "a"},
                            timeout=10,
                        ),
                        ac.post(
                            "/message",
                            json={"message": "y", "conversation_id": "b"},
                            timeout=10,
                        ),
                    )
                    elapsed = asyncio.get_event_loop().time() - start
                    assert all(r.status_code == 200 for r in responses)
                    return elapsed

        elapsed = asyncio.run(run())
        assert elapsed < 0.28, f"expected <0.28 s, got {elapsed:.3f}"


class TestUpload:
    def test_upload_happy_path(self, client, server_workspace):
        files = {"file": ("hello.txt", b"hello world", "text/plain")}
        data = {"conversation_id": "c-test"}
        r = client.post("/upload", files=files, data=data)
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["filename"] == "hello.txt"
        assert body["mime_type"] == "text/plain"
        assert body["size_bytes"] == 11
        assert body["conversation_id"] == "c-test"
        assert body["file_id"].endswith("__hello.txt")
        on_disk = (
            server_workspace.root / ".blueclaw" / "uploads" / "c-test" / body["file_id"]
        )
        assert on_disk.read_bytes() == b"hello world"

    def test_upload_generates_tmp_cid_when_omitted(self, client):
        files = {"file": ("hello.txt", b"hi", "text/plain")}
        r = client.post("/upload", files=files)
        assert r.status_code == 201
        assert r.json()["conversation_id"].startswith("tmp-")

    def test_upload_rejects_oversize(self, client, monkeypatch):
        from blueclaw import uploads as uploads_mod

        monkeypatch.setattr(uploads_mod, "MAX_UPLOAD_BYTES", 16)
        files = {"file": ("big.txt", b"x" * 32, "text/plain")}
        r = client.post("/upload", files=files, data={"conversation_id": "c-test"})
        assert r.status_code == 413

    def test_upload_rejects_oversize_content_length(self, client):
        """413 fires from the Content-Length pre-check before any body is read."""
        from blueclaw.uploads import MAX_UPLOAD_BYTES

        r = client.post(
            "/upload",
            content=b"",
            headers={
                "Content-Length": str(MAX_UPLOAD_BYTES + 1),
                "Content-Type": "multipart/form-data; boundary=x",
            },
        )
        assert r.status_code == 413

    def test_upload_rejects_disallowed_mime(self, client):
        files = {"file": ("evil.exe", b"MZ\x90\x00binary", "application/octet-stream")}
        r = client.post("/upload", files=files, data={"conversation_id": "c-test"})
        assert r.status_code == 415

    def test_upload_requires_bearer_when_configured(
        self, server_config, server_workspace, mock_agent_result
    ):
        files = {"file": ("a.txt", b"hi", "text/plain")}
        with (
            patch("blueclaw.runner.create_agent") as mock_ca,
            patch(
                "blueclaw.runner.build_trace_and_record",
                side_effect=_fake_build_trace_and_record,
            ),
            patch("blueclaw.runner.cleanup_mcp_clients"),
            patch.object(server_workspace, "write_trace"),
            patch.object(server_workspace, "append_history"),
            patch.dict(os.environ, {"BLUECLAW_API_KEY": "secret"}),
        ):
            mock_ca.return_value.return_value = mock_agent_result
            app = create_server_app(server_config, server_workspace, model=MagicMock())
            tc = TestClient(app)

            r = tc.post("/upload", files=files, data={"conversation_id": "c-test"})
            assert r.status_code == 401
            r = tc.post(
                "/upload",
                files=files,
                data={"conversation_id": "c-test"},
                headers={"Authorization": "Bearer wrong"},
            )
            assert r.status_code == 401
            r = tc.post(
                "/upload",
                files=files,
                data={"conversation_id": "c-test"},
                headers={"Authorization": "Bearer secret"},
            )
            assert r.status_code == 201


class TestMessageAttachments:
    def test_file_ids_prepend_path_prefix_to_prompt(
        self, server_config, server_workspace, mock_agent_result
    ):
        captured = {}

        def fake_agent_callable(prompt):
            captured["prompt"] = prompt
            return mock_agent_result

        with (
            patch("blueclaw.runner.create_agent") as mock_ca,
            patch(
                "blueclaw.runner.build_trace_and_record",
                side_effect=_fake_build_trace_and_record,
            ),
            patch("blueclaw.runner.cleanup_mcp_clients"),
            patch.object(server_workspace, "write_trace"),
            patch.object(server_workspace, "append_history"),
            patch.dict(os.environ, {"BLUECLAW_API_KEY": ""}),
        ):
            mock_ca.return_value = fake_agent_callable
            app = create_server_app(server_config, server_workspace, model=MagicMock())
            tc = TestClient(app)

            up = tc.post(
                "/upload",
                files={"file": ("hello.txt", b"hello", "text/plain")},
                data={"conversation_id": "c-1"},
            )
            assert up.status_code == 201, up.text
            file_id = up.json()["file_id"]

            r = tc.post(
                "/message",
                json={
                    "message": "summarize",
                    "conversation_id": "c-1",
                    "file_ids": [file_id],
                },
            )
            assert r.status_code == 200, r.text
            prompt = captured["prompt"]
            assert "User attached the following files" in prompt
            assert file_id in prompt
            assert "summarize" in prompt
            assert prompt.index(file_id) < prompt.index("summarize")

    def test_message_rejects_unknown_file_id(self, client):
        r = client.post(
            "/message",
            json={
                "message": "hi",
                "conversation_id": "c-1",
                "file_ids": ["00000000-0000-0000-0000-000000000000__x.txt"],
            },
        )
        assert r.status_code == 400
        assert "file_id" in r.json()["error"].lower()

    def test_message_rejects_too_many_file_ids(self, client):
        r = client.post(
            "/message",
            json={
                "message": "hi",
                "conversation_id": "c-1",
                "file_ids": [f"id-{i}__x.txt" for i in range(11)],
            },
        )
        assert r.status_code == 400
        assert "10" in r.json()["error"]

    def test_image_attachment_routed_as_native_block(
        self, server_config, server_workspace, mock_agent_result
    ):
        """Image attachment becomes a Strands ContentBlock list with image bytes."""
        captured = {}

        def fake_agent_callable(prompt):
            captured["prompt"] = prompt
            return mock_agent_result

        # Minimal valid 1x1 PNG (8-byte sig + IHDR + IDAT + IEND)
        png_bytes = (
            b"\x89PNG\r\n\x1a\n"
            b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
            b"\x1f\x15\xc4\x89"
            b"\x00\x00\x00\rIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01"
            b"\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
        )

        with (
            patch("blueclaw.runner.create_agent") as mock_ca,
            patch(
                "blueclaw.runner.build_trace_and_record",
                side_effect=_fake_build_trace_and_record,
            ),
            patch("blueclaw.runner.cleanup_mcp_clients"),
            patch.object(server_workspace, "write_trace"),
            patch.object(server_workspace, "append_history"),
            patch.dict(os.environ, {"BLUECLAW_API_KEY": ""}),
        ):
            mock_ca.return_value = fake_agent_callable
            app = create_server_app(server_config, server_workspace, model=MagicMock())
            tc = TestClient(app)

            up = tc.post(
                "/upload",
                files={"file": ("pic.png", png_bytes, "image/png")},
                data={"conversation_id": "c-img"},
            )
            assert up.status_code == 201, up.text
            file_id = up.json()["file_id"]

            r = tc.post(
                "/message",
                json={
                    "message": "what is in this image?",
                    "conversation_id": "c-img",
                    "file_ids": [file_id],
                },
            )
            assert r.status_code == 200, r.text
            blocks = captured["prompt"]
            assert isinstance(
                blocks, list
            ), "image attachments should produce ContentBlock list"
            image_blocks = [b for b in blocks if "image" in b]
            assert len(image_blocks) == 1
            assert image_blocks[0]["image"]["format"] == "png"
            assert image_blocks[0]["image"]["source"]["bytes"] == png_bytes
            text_blocks = [b for b in blocks if "text" in b]
            assert len(text_blocks) == 1
            assert "what is in this image?" in text_blocks[0]["text"]

    def test_pdf_attachment_uses_path_prefix(
        self, server_config, server_workspace, mock_agent_result
    ):
        """Non-image attachments stay on the path-prefix flow (plain string)."""
        captured = {}

        def fake_agent_callable(prompt):
            captured["prompt"] = prompt
            return mock_agent_result

        pdf_bytes = b"%PDF-1.4\n%minimal\n"
        with (
            patch("blueclaw.runner.create_agent") as mock_ca,
            patch(
                "blueclaw.runner.build_trace_and_record",
                side_effect=_fake_build_trace_and_record,
            ),
            patch("blueclaw.runner.cleanup_mcp_clients"),
            patch.object(server_workspace, "write_trace"),
            patch.object(server_workspace, "append_history"),
            patch.dict(os.environ, {"BLUECLAW_API_KEY": ""}),
        ):
            mock_ca.return_value = fake_agent_callable
            app = create_server_app(server_config, server_workspace, model=MagicMock())
            tc = TestClient(app)

            up = tc.post(
                "/upload",
                files={"file": ("doc.pdf", pdf_bytes, "application/pdf")},
                data={"conversation_id": "c-pdf"},
            )
            file_id = up.json()["file_id"]

            r = tc.post(
                "/message",
                json={
                    "message": "summarize",
                    "conversation_id": "c-pdf",
                    "file_ids": [file_id],
                },
            )
            assert r.status_code == 200
            assert isinstance(captured["prompt"], str)
            assert "User attached the following files" in captured["prompt"]

    def test_message_rejects_file_ids_without_cid(self, client):
        r = client.post(
            "/message",
            json={"message": "hi", "file_ids": ["any__x.txt"]},
        )
        assert r.status_code == 400


class TestStatefulStream:
    def test_stream_passes_session_manager_when_conversation_id_set(
        self, server_config, server_workspace, mock_agent_result
    ):
        with (
            patch("blueclaw.runner.create_agent") as mock_ca,
            patch(
                "blueclaw.runner.build_trace_and_record",
                side_effect=_fake_build_trace_and_record,
            ) as mock_btr,
            patch("blueclaw.runner.cleanup_mcp_clients"),
            patch.object(server_workspace, "write_trace"),
            patch.object(server_workspace, "append_history"),
            patch.dict(os.environ, {"BLUECLAW_API_KEY": ""}),
            patch("blueclaw.server.FileSessionManager") as mock_fsm,
        ):
            agent = MagicMock()

            async def fake_stream(msg):
                yield {"data": "hello"}
                yield {"result": mock_agent_result}

            agent.stream_async = fake_stream
            mock_ca.return_value = agent
            app = create_server_app(server_config, server_workspace, model=MagicMock())
            with TestClient(app) as tc:
                r = tc.post(
                    "/message/stream",
                    json={"message": "hi", "conversation_id": "cs"},
                )
            assert r.status_code == 200
            mock_fsm.assert_called_once()
            assert mock_fsm.call_args.kwargs["session_id"] == "cs"
            assert mock_btr.call_args.kwargs["conversation_id"] == "cs"

    def test_stream_no_session_manager_when_id_absent(
        self, server_config, server_workspace, mock_agent_result
    ):
        with (
            patch("blueclaw.runner.create_agent") as mock_ca,
            patch(
                "blueclaw.runner.build_trace_and_record",
                side_effect=_fake_build_trace_and_record,
            ),
            patch("blueclaw.runner.cleanup_mcp_clients"),
            patch.object(server_workspace, "write_trace"),
            patch.object(server_workspace, "append_history"),
            patch.dict(os.environ, {"BLUECLAW_API_KEY": ""}),
            patch("blueclaw.server.FileSessionManager") as mock_fsm,
        ):
            agent = MagicMock()

            async def fake_stream(msg):
                yield {"data": "hi"}
                yield {"result": mock_agent_result}

            agent.stream_async = fake_stream
            mock_ca.return_value = agent
            app = create_server_app(server_config, server_workspace, model=MagicMock())
            with TestClient(app) as tc:
                tc.post("/message/stream", json={"message": "hi"})
            mock_fsm.assert_not_called()
