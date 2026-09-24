"""Stop is acknowledged only after the underlying invocation has ended."""

import asyncio
import json
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest

from blueclaw.models import SessionConfig
from blueclaw.server import create_server_app
from blueclaw.workspace import Workspace


class ControlledAgent:
    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = False
        self.messages = []

    def cancel(self):
        self.cancelled = True

    async def stream_async(self, prompt):
        self.messages.append({"role": "user", "content": [{"text": str(prompt)}]})
        self.started.set()
        yield {"data": "prefix"}
        await self.release.wait()
        result = SimpleNamespace(
            message={"role": "assistant", "content": [{"text": "placeholder"}]},
            stop_reason="cancelled" if self.cancelled else "end_turn",
            metrics=SimpleNamespace(
                accumulated_usage={
                    "inputTokens": 1,
                    "outputTokens": 1,
                    "totalTokens": 2,
                }
            ),
        )
        yield {"result": result}


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/message", "/message/stream"])
async def test_stop_acknowledgement_waits_for_invocation_release(tmp_path: Path, path):
    workspace = Workspace(tmp_path / "ws")
    config = SessionConfig(workspace_path=workspace.root, tools=[])
    agent = ControlledAgent()
    with (
        patch("blueclaw.runner.create_agent", return_value=agent),
        patch("blueclaw.server.BackgroundContextUpdater") as updater,
    ):
        updater.return_value.trigger.return_value = None
        app = create_server_app(config, workspace, model=object())
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            pending = asyncio.create_task(
                client.post(
                    path,
                    json={
                        "message": "work",
                        "conversation_id": "c",
                        "request_id": "r",
                    },
                )
            )
            try:
                await asyncio.wait_for(agent.started.wait(), 2)
                stop = await client.post("/requests/r/cancel")
                assert stop.status_code == 202
                assert (await client.post("/requests/r/cancel")).status_code == 202
                await asyncio.sleep(0)
                assert not pending.done()
                agent.release.set()
                response = await asyncio.wait_for(pending, 2)
            finally:
                agent.release.set()
                if not pending.done():
                    await asyncio.wait_for(pending, 2)
    if path.endswith("stream"):
        assert "event: stopped" in response.text
        assert "event: done" not in response.text
    else:
        assert response.json()["status"] == "cancelled"
    captures = list(workspace.root.rglob("response.txt"))
    assert len(captures) == 1
    assert captures[0].read_text() == "prefix"


@pytest.mark.asyncio
async def test_completed_request_id_is_no_longer_cancellable(tmp_path):
    workspace = Workspace(tmp_path / "ws")
    agent = ControlledAgent()
    agent.release.set()
    with (
        patch("blueclaw.runner.create_agent", return_value=agent),
        patch("blueclaw.server.BackgroundContextUpdater"),
    ):
        app = create_server_app(
            SessionConfig(workspace_path=workspace.root, tools=[]),
            workspace,
            model=object(),
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/message",
                json={
                    "message": "work",
                    "request_id": "finished",
                },
                headers={"Origin": "http://localhost:3000"},
            )
            assert response.status_code == 200
            assert response.headers["X-Blueclaw-Request-ID"] == "finished"
            assert (
                "X-Blueclaw-Request-ID"
                in response.headers["Access-Control-Expose-Headers"]
            )
            assert (await client.post("/requests/finished/cancel")).status_code == 404


@pytest.mark.asyncio
async def test_timeout_waits_for_agent_then_returns_partial_capture(tmp_path):
    workspace = Workspace(tmp_path / "ws")
    agent = ControlledAgent()
    with (
        patch("blueclaw.runner.create_agent", return_value=agent),
        patch("blueclaw.server.BackgroundContextUpdater"),
        patch("blueclaw.server._TIMEOUT", 0.01),
    ):
        app = create_server_app(
            SessionConfig(workspace_path=workspace.root, tools=[]),
            workspace,
            model=object(),
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            pending = asyncio.create_task(
                client.post(
                    "/message",
                    json={
                        "message": "work",
                        "request_id": "timed",
                    },
                )
            )
            await asyncio.wait_for(agent.started.wait(), 2)
            await asyncio.sleep(0.05)
            assert agent.cancelled
            assert not pending.done()
            agent.release.set()
            response = await asyncio.wait_for(pending, 2)
    assert response.status_code == 504
    assert response.json()["termination_reason"] == "timeout"
    assert response.json()["reply"] == "prefix"
    assert next(workspace.root.rglob("response.txt")).read_text() == "prefix"


@pytest.mark.asyncio
@pytest.mark.parametrize("same_conversation", [True, False])
async def test_second_request_waits_and_can_stop_before_agent_construction(
    tmp_path, same_conversation
):
    workspace = Workspace(tmp_path / "ws")
    config = SessionConfig(
        workspace_path=workspace.root, tools=[], max_concurrent_runs=1
    )
    agent = ControlledAgent()
    with (
        patch("blueclaw.runner.create_agent", return_value=agent) as factory,
        patch("blueclaw.server.BackgroundContextUpdater"),
    ):
        app = create_server_app(config, workspace, model=object())
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            first = asyncio.create_task(
                client.post(
                    "/message",
                    json={
                        "message": "first",
                        "conversation_id": "c" if same_conversation else "d",
                        "request_id": "first",
                    },
                )
            )
            await asyncio.wait_for(agent.started.wait(), 2)
            second = asyncio.create_task(
                client.post(
                    "/message",
                    json={
                        "message": "second",
                        "conversation_id": "c",
                        "request_id": "second",
                    },
                )
            )
            try:
                await asyncio.sleep(0.05)
                assert factory.call_count == 1
                stop = await client.post("/requests/second/cancel")
                assert stop.status_code == 202
                answer = await asyncio.wait_for(second, 2)
                assert answer.json()["status"] == "cancelled"
                assert factory.call_count == 1
            finally:
                agent.release.set()
                await asyncio.wait_for(first, 2)
    paths = list(workspace.root.rglob("response.txt"))
    assert len(paths) == 2
    assert len({str(p.parent) for p in paths}) == 2


@pytest.mark.asyncio
async def test_cancel_route_auth_validation_and_unknown_ids(tmp_path, monkeypatch):
    workspace = Workspace(tmp_path / "ws")
    app = create_server_app(
        SessionConfig(workspace_path=workspace.root), workspace, model=object()
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        monkeypatch.setenv("BLUECLAW_API_KEY", "secret")
        assert (await client.post("/requests/r/cancel")).status_code == 401
        headers = {"Authorization": "Bearer secret"}
        assert (
            await client.post("/requests/invalid.id/cancel", headers=headers)
        ).status_code == 400
        assert (
            await client.post("/requests/missing/cancel", headers=headers)
        ).status_code == 404
        rejected = await client.post(
            "/message",
            json={
                "message": "hi",
                "request_id": "bad.id",
            },
            headers=headers,
        )
        assert rejected.status_code == 400
        assert "bad.id" not in rejected.text


@pytest.mark.asyncio
async def test_duplicate_request_id_is_rejected_without_replacing_owner(tmp_path):
    workspace = Workspace(tmp_path / "ws")
    config = SessionConfig(workspace_path=workspace.root, tools=[])
    agent = ControlledAgent()
    with (
        patch("blueclaw.runner.create_agent", return_value=agent),
        patch("blueclaw.server.BackgroundContextUpdater"),
    ):
        app = create_server_app(config, workspace, model=object())
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            first = asyncio.create_task(
                client.post(
                    "/message",
                    json={
                        "message": "first",
                        "request_id": "same",
                    },
                )
            )
            await asyncio.wait_for(agent.started.wait(), 2)
            try:
                duplicate = await client.post(
                    "/message",
                    json={
                        "message": "second",
                        "request_id": "same",
                    },
                )
                assert duplicate.status_code == 409
                assert (await client.post("/requests/same/cancel")).status_code == 202
            finally:
                agent.release.set()
                await asyncio.wait_for(first, 2)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["missing_result", "exception"])
async def test_incomplete_invocation_keeps_prefix_and_error_record(tmp_path, failure):
    workspace = Workspace(tmp_path / "ws")
    config = SessionConfig(workspace_path=workspace.root, tools=[])

    class FailingAgent(ControlledAgent):
        async def stream_async(self, prompt):
            self.messages.append({"role": "user", "content": [{"text": str(prompt)}]})
            yield {"data": "prefix"}
            if failure == "exception":
                raise RuntimeError("fixture failed")

    agent = FailingAgent()
    with (
        patch("blueclaw.runner.create_agent", return_value=agent),
        patch("blueclaw.server.BackgroundContextUpdater"),
    ):
        app = create_server_app(config, workspace, model=object())
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/message",
                json={
                    "message": "work",
                    "request_id": failure,
                },
            )
    assert response.status_code == 500
    assert response.json()["status"] == "error"
    assert response.json()["reply"] == "prefix"
    assert next(workspace.root.rglob("response.txt")).read_text() == "prefix"
    traces = workspace.list_traces()
    assert len(traces) == 1
    assert traces[0].usage_complete is False


@pytest.mark.asyncio
async def test_cleanup_failure_does_not_report_a_clean_stop(tmp_path):
    workspace = Workspace(tmp_path / "ws")
    config = SessionConfig(workspace_path=workspace.root, tools=[])
    agent = ControlledAgent()
    with (
        patch("blueclaw.runner.create_agent", return_value=agent),
        patch("blueclaw.server.BackgroundContextUpdater"),
        patch(
            "blueclaw.runner.cleanup_mcp_clients",
            return_value=[
                {
                    "client": "fixture",
                    "error": "close failed",
                }
            ],
        ),
    ):
        app = create_server_app(config, workspace, model=object())
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            pending = asyncio.create_task(
                client.post(
                    "/message",
                    json={
                        "message": "work",
                        "request_id": "cleanup",
                    },
                )
            )
            await asyncio.wait_for(agent.started.wait(), 2)
            assert (await client.post("/requests/cleanup/cancel")).status_code == 202
            agent.release.set()
            response = await asyncio.wait_for(pending, 2)
    assert response.status_code == 500
    assert response.json()["status"] == "error"
    assert response.json()["termination_reason"] == "cleanup_failed"
    assert response.json()["original_termination_reason"] == "user"


@pytest.mark.asyncio
async def test_capture_directory_error_does_not_abort_invocation(tmp_path):
    workspace = Workspace(tmp_path / "ws")
    config = SessionConfig(workspace_path=workspace.root, tools=[])
    agent = ControlledAgent()
    agent.release.set()
    with (
        patch("blueclaw.runner.create_agent", return_value=agent),
        patch("blueclaw.server.BackgroundContextUpdater"),
        patch("blueclaw.server.next_capture_path", side_effect=OSError("disk full")),
    ):
        app = create_server_app(config, workspace, model=object())
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post("/message", json={"message": "work"})
    assert response.status_code == 200
    assert response.json()["status"] == "success"
    assert len(workspace.list_traces()) == 1
    assert workspace.list_traces()[0].capture_path is None


@pytest.mark.asyncio
async def test_stream_disconnect_stops_owned_run_and_keeps_capture(tmp_path):
    workspace = Workspace(tmp_path / "ws")
    agent = ControlledAgent()
    with (
        patch("blueclaw.runner.create_agent", return_value=agent),
        patch("blueclaw.server.BackgroundContextUpdater"),
    ):
        app = create_server_app(
            SessionConfig(workspace_path=workspace.root, tools=[]),
            workspace,
            model=object(),
        )
        body = json.dumps({"message": "work", "request_id": "disconnect"}).encode()
        received = asyncio.Queue()
        await received.put({"type": "http.request", "body": body, "more_body": False})
        body_seen = asyncio.Event()

        async def receive():
            return await received.get()

        async def send(message):
            if message["type"] == "http.response.body" and message.get("body"):
                body_seen.set()

        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/message/stream",
            "raw_path": b"/message/stream",
            "query_string": b"",
            "headers": [(b"content-type", b"application/json")],
            "client": ("127.0.0.1", 1234),
            "server": ("test", 80),
        }
        serving = asyncio.create_task(app(scope, receive, send))
        try:
            await asyncio.wait_for(agent.started.wait(), 2)
            await asyncio.wait_for(body_seen.wait(), 2)
            await received.put({"type": "http.disconnect"})
            await asyncio.wait_for(serving, 2)
            for _ in range(100):
                if agent.cancelled:
                    break
                await asyncio.sleep(0.01)
            assert agent.cancelled
        finally:
            agent.release.set()
            await asyncio.wait_for(serving, 2)
    for _ in range(100):
        if workspace.list_traces():
            break
        await asyncio.sleep(0.01)
    assert workspace.list_traces()[0].termination_reason == "disconnect"
    assert next(workspace.root.rglob("response.txt")).read_text() == "prefix"


@pytest.mark.asyncio
async def test_nonstream_disconnect_requests_stop_and_keeps_capture(tmp_path):
    workspace = Workspace(tmp_path / "ws")
    agent = ControlledAgent()
    with (
        patch("blueclaw.runner.create_agent", return_value=agent),
        patch("blueclaw.server.BackgroundContextUpdater"),
    ):
        app = create_server_app(
            SessionConfig(workspace_path=workspace.root, tools=[]),
            workspace,
            model=object(),
        )
        body = json.dumps({"message": "work", "request_id": "disconnect"}).encode()
        received = asyncio.Queue()
        await received.put({"type": "http.request", "body": body, "more_body": False})

        async def receive():
            return await received.get()

        async def send(message):
            pass

        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/message",
            "raw_path": b"/message",
            "query_string": b"",
            "headers": [(b"content-type", b"application/json")],
            "client": ("127.0.0.1", 1234),
            "server": ("test", 80),
        }
        serving = asyncio.create_task(app(scope, receive, send))
        try:
            await asyncio.wait_for(agent.started.wait(), 2)
            await received.put({"type": "http.disconnect"})
            for _ in range(100):
                if agent.cancelled:
                    break
                await asyncio.sleep(0.01)
            assert agent.cancelled
        finally:
            agent.release.set()
            await asyncio.wait_for(serving, 2)
    assert workspace.list_traces()[0].termination_reason == "disconnect"
    assert next(workspace.root.rglob("response.txt")).read_text() == "prefix"


@pytest.mark.asyncio
async def test_stop_during_cleanup_reports_already_finishing(tmp_path):
    workspace = Workspace(tmp_path / "ws")
    agent = ControlledAgent()
    agent.release.set()
    cleanup_entered = threading.Event()
    cleanup_release = threading.Event()

    def slow_cleanup(observer):
        cleanup_entered.set()
        cleanup_release.wait(2)
        return []

    with (
        patch("blueclaw.runner.create_agent", return_value=agent),
        patch("blueclaw.server.BackgroundContextUpdater"),
        patch("blueclaw.runner.cleanup_mcp_clients", side_effect=slow_cleanup),
    ):
        app = create_server_app(
            SessionConfig(workspace_path=workspace.root, tools=[]),
            workspace,
            model=object(),
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            pending = asyncio.create_task(
                client.post(
                    "/message",
                    json={
                        "message": "work",
                        "request_id": "race",
                    },
                )
            )
            try:
                assert await asyncio.to_thread(cleanup_entered.wait, 2)
                stop = await client.post("/requests/race/cancel")
                assert stop.status_code == 409
                assert stop.json()["error"] == "already_finishing"
                assert not pending.done()
            finally:
                cleanup_release.set()
                response = await asyncio.wait_for(pending, 2)
    assert response.status_code == 200
    assert response.json()["status"] == "success"
