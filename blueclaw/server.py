"""Agent API Gateway. One model per app; per-request observer isolation.

v2.1: asyncio.Semaphore caps simultaneous agent runs; POST /message/stream
emits Server-Sent Events for token-by-token output.
"""

from __future__ import annotations
import asyncio
import hmac
import json
import os
from datetime import datetime, timezone
from io import StringIO
from typing import Any
from uuid import uuid4
from pydantic import ValidationError
from rich.console import Console
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route
from blueclaw import __version__
from blueclaw.models import (
    MessageRequest,
    MessageResponse,
    SessionConfig,
    calculate_cost,
)
from blueclaw.observer import ObserverHooks
from blueclaw.session import (
    build_trace_and_record,
    cleanup_mcp_clients,
    create_agent,
    extract_text,
)
from blueclaw.workspace import Workspace, WorkspaceError

_BODY_LIMIT = 1_048_576
_TIMEOUT = 300


def _authenticate(request: Request) -> bool:
    """True when no key configured, or Bearer token matches BLUECLAW_API_KEY."""
    api_key = os.environ.get("BLUECLAW_API_KEY", "")
    if not api_key:
        return True
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return False
    return hmac.compare_digest(auth[7:], api_key)  # len("Bearer ") == 7


def _sse(event: str, data: dict) -> str:
    """Encode a single Server-Sent Event."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def _parse_request(
    request: Request,
) -> tuple[MessageRequest | None, JSONResponse | None]:
    """Run auth + body-size + JSON validation. Returns (req, None) on success
    or (None, error_response) on failure."""
    if not _authenticate(request):
        return None, JSONResponse({"error": "unauthorized"}, status_code=401)
    cl = request.headers.get("content-length")
    if cl and int(cl) > _BODY_LIMIT:
        return None, JSONResponse({"error": "payload too large"}, status_code=413)
    body = await request.body()
    if len(body) > _BODY_LIMIT:
        return None, JSONResponse({"error": "payload too large"}, status_code=413)
    try:
        req = MessageRequest(**json.loads(body))
    except (json.JSONDecodeError, ValidationError, TypeError) as exc:
        return None, JSONResponse({"error": str(exc)}, status_code=400)
    return req, None


def _make_run_id(start_time: datetime) -> str:
    return f"{start_time:%Y%m%d-%H%M%S}-{uuid4().hex[:4]}"


def _build_response_payload(
    result: Any,
    req: MessageRequest,
    config: SessionConfig,
    run_id: str,
) -> dict:
    """Extract MessageResponse payload from an AgentResult."""
    usage = (
        result.metrics.accumulated_usage
        if isinstance(result.metrics.accumulated_usage, dict)
        else {}
    )
    return MessageResponse(
        reply=extract_text(result.message),
        run_id=run_id,
        conversation_id=req.conversation_id,
        tokens=usage.get("totalTokens", 0),
        cost=calculate_cost(
            config.model_id,
            usage.get("inputTokens", 0),
            usage.get("outputTokens", 0),
        ),
    ).model_dump()


class _LockRegistry:
    """Per-key asyncio.Lock map. Lock creation is itself guarded by a
    meta-lock so concurrent first-time `get(key)` calls return the same
    lock object."""

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._meta = asyncio.Lock()

    async def get(self, key: str) -> asyncio.Lock:
        async with self._meta:
            lock = self._locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[key] = lock
            return lock


def create_server_app(
    config: SessionConfig,
    workspace: Workspace,
    model=None,
    cors_origin: str | None = None,
) -> Starlette:
    """App factory. model is injectable for tests; None → build_model(config)."""
    if model is None:
        from blueclaw.session import build_model

        model = build_model(config)
    workspace.purge_old_traces(config.trace_retention_days)
    workspace.purge_old_sessions(config.trace_retention_days)

    semaphore = asyncio.Semaphore(config.max_concurrent_runs)

    async def health(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "version": __version__})

    async def handle_message(request: Request) -> JSONResponse:
        req, err = await _parse_request(request)
        if err is not None:
            return err
        observer = None
        try:
            async with semaphore:
                observer = ObserverHooks(console=Console(file=StringIO()), quiet=True)
                agent = create_agent(
                    config,
                    workspace,
                    observer,
                    model=model,
                    scripted=True,
                    callback_handler=None,
                )
                start_time = datetime.now(timezone.utc)
                result = await asyncio.wait_for(
                    asyncio.to_thread(agent, req.message), timeout=_TIMEOUT
                )
                end_time = datetime.now(timezone.utc)
                run_id = _make_run_id(start_time)
                trace, record = build_trace_and_record(
                    result,
                    req.message,
                    observer,
                    config,
                    run_id,
                    start_time,
                    end_time,
                    source="api",
                )
                workspace.write_trace(trace)
                workspace.append_history(record)
                return JSONResponse(
                    _build_response_payload(result, req, config, run_id)
                )
        except asyncio.TimeoutError:
            return JSONResponse({"error": "agent timed out"}, status_code=504)
        except WorkspaceError as exc:
            return JSONResponse({"error": f"workspace error: {exc}"}, status_code=500)
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)
        finally:
            cleanup_mcp_clients(observer)

    async def handle_message_stream(request: Request):
        req, err = await _parse_request(request)
        if err is not None:
            return err

        async def event_stream():
            observer = None
            try:
                async with semaphore:
                    observer = ObserverHooks(
                        console=Console(file=StringIO()), quiet=True
                    )
                    agent = create_agent(
                        config,
                        workspace,
                        observer,
                        model=model,
                        scripted=True,
                        callback_handler=None,
                    )
                    start_time = datetime.now(timezone.utc)
                    final_result: Any = None
                    try:
                        async with asyncio.timeout(_TIMEOUT):
                            async for event in agent.stream_async(req.message):
                                chunk = (
                                    event.get("data")
                                    if isinstance(event, dict)
                                    else None
                                )
                                if chunk:
                                    yield _sse("delta", {"text": chunk})
                                if (
                                    isinstance(event, dict)
                                    and event.get("result") is not None
                                ):
                                    final_result = event["result"]
                    except asyncio.TimeoutError:
                        yield _sse("error", {"error": "agent timed out"})
                        return

                    if final_result is None:
                        yield _sse("error", {"error": "agent did not return a result"})
                        return

                    end_time = datetime.now(timezone.utc)
                    run_id = _make_run_id(start_time)
                    try:
                        trace, record = build_trace_and_record(
                            final_result,
                            req.message,
                            observer,
                            config,
                            run_id,
                            start_time,
                            end_time,
                            source="api",
                        )
                        workspace.write_trace(trace)
                        workspace.append_history(record)
                    except WorkspaceError as exc:
                        yield _sse("error", {"error": f"workspace error: {exc}"})
                        return

                    yield _sse(
                        "done",
                        _build_response_payload(final_result, req, config, run_id),
                    )
            except Exception as exc:
                yield _sse("error", {"error": str(exc)})
            finally:
                cleanup_mcp_clients(observer)

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    app = Starlette(
        routes=[
            Route("/health", health, methods=["GET"]),
            Route("/message", handle_message, methods=["POST"]),
            Route("/message/stream", handle_message_stream, methods=["POST"]),
        ]
    )
    return CORSMiddleware(
        app,
        allow_origins=[cors_origin] if cors_origin else [],
        allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
        allow_methods=["*"],
        allow_headers=["*"],
    )
