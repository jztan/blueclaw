"""Agent API Gateway. One model per app; per-request observer isolation.
TODO v2.1: asyncio.Semaphore, SSE streaming."""

from __future__ import annotations
import asyncio
import hmac
import json
import os
from datetime import datetime, timezone
from io import StringIO
from uuid import uuid4
from pydantic import ValidationError
from rich.console import Console
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
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

    async def health(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "version": __version__})

    async def handle_message(request: Request) -> JSONResponse:
        observer = None
        try:
            if not _authenticate(request):
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            cl = request.headers.get("content-length")
            if cl and int(cl) > _BODY_LIMIT:
                return JSONResponse({"error": "payload too large"}, status_code=413)
            body = await request.body()
            if len(body) > _BODY_LIMIT:
                return JSONResponse({"error": "payload too large"}, status_code=413)
            try:
                req = MessageRequest(**json.loads(body))
            except (json.JSONDecodeError, ValidationError, TypeError) as exc:
                return JSONResponse({"error": str(exc)}, status_code=400)
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
            run_id = f"{start_time:%Y%m%d-%H%M%S}-{uuid4().hex[:4]}"
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
            u = (
                result.metrics.accumulated_usage
                if isinstance(result.metrics.accumulated_usage, dict)
                else {}
            )
            return JSONResponse(
                MessageResponse(
                    reply=extract_text(result.message),
                    run_id=run_id,
                    conversation_id=req.conversation_id,
                    tokens=u.get("totalTokens", 0),
                    cost=calculate_cost(
                        config.model_id,
                        u.get("inputTokens", 0),
                        u.get("outputTokens", 0),
                    ),
                ).model_dump()
            )
        except asyncio.TimeoutError:
            return JSONResponse({"error": "agent timed out"}, status_code=504)
        except WorkspaceError as exc:
            return JSONResponse({"error": f"workspace error: {exc}"}, status_code=500)
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)
        finally:
            cleanup_mcp_clients(observer)

    app = Starlette(
        routes=[
            Route("/health", health, methods=["GET"]),
            Route("/message", handle_message, methods=["POST"]),
        ]
    )
    return CORSMiddleware(
        app,
        allow_origins=[cors_origin] if cors_origin else [],
        allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
        allow_methods=["*"],
        allow_headers=["*"],
    )
