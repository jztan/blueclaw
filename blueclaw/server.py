"""Agent API Gateway. One model per app; per-request observer isolation.

v2.1: asyncio.Semaphore caps simultaneous agent runs; POST /message/stream
emits Server-Sent Events for token-by-token output.
"""

from __future__ import annotations
import asyncio
import contextlib
import hmac
import json
import logging
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, StreamingResponse
from starlette.routing import Route

_PLAYGROUND_HTML = (Path(__file__).parent / "static" / "playground.html").read_text()
from blueclaw import __version__
from blueclaw.models import (
    MessageRequest,
    MessageResponse,
    SessionConfig,
    UploadResponse,
    calculate_cost,
)
from blueclaw.uploads import (
    MAX_UPLOAD_BYTES,
    UploadError,
    UploadStore,
    build_agent_input,
)
from blueclaw.session import (
    BackgroundContextUpdater,
    extract_text,
)
from blueclaw.runner import (
    finalize,
    next_capture_path,
    runner_session,
    validate_session_id,
)
from blueclaw.workspace import Workspace, WorkspaceError
from strands.session.file_session_manager import FileSessionManager

_BODY_LIMIT = 1_048_576
_TIMEOUT = 300
_MAX_ATTACHMENTS = 10

logger = logging.getLogger(__name__)


def _resolve_attachments(
    store: "UploadStore",
    cid: str | None,
    file_ids: list[str],
) -> tuple[list, JSONResponse | None]:
    """Resolve file_ids → UploadRecord list. Returns (records, error_response).

    On error, returns (empty list, JSONResponse). Caller must early-return the response.
    """
    if not file_ids:
        return [], None
    if len(file_ids) > _MAX_ATTACHMENTS:
        return [], JSONResponse(
            {"error": f"too many attachments (max {_MAX_ATTACHMENTS})"},
            status_code=400,
        )
    if cid is None:
        return [], JSONResponse(
            {"error": "conversation_id required when file_ids are provided"},
            status_code=400,
        )
    records = []
    for fid in file_ids:
        try:
            records.append(store.resolve(cid, fid))
        except UploadError as exc:
            return [], JSONResponse({"error": f"file_id error: {exc}"}, status_code=400)
    return records, None


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
    except ValidationError as exc:
        # Pydantic echoes the rejected input_value in str(exc). For
        # conversation_id failures that's a path-traversal leak, so collapse
        # to a generic message instead of forwarding the validation detail.
        for err in exc.errors():
            if "conversation_id" in err.get("loc", ()):
                return None, JSONResponse(
                    {"error": "invalid conversation_id"}, status_code=400
                )
        return None, JSONResponse({"error": str(exc)}, status_code=400)
    except (json.JSONDecodeError, TypeError) as exc:
        return None, JSONResponse({"error": str(exc)}, status_code=400)
    return req, None


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
            usage.get("cacheReadInputTokens", 0),
            usage.get("cacheWriteInputTokens", 0),
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
    conv_locks = _LockRegistry()
    sessions_dir = str(workspace.root / ".blueclaw" / "sessions")
    uploads_root = workspace.root / ".blueclaw" / "uploads"
    upload_store = UploadStore(uploads_root)

    # Per-turn CONTEXT.md updater. trigger() is no-op if a previous update is
    # still running, so concurrent turns across conversations can't race and
    # never queue more than one writer at a time. The shutdown handler waits
    # on the last in-flight thread so Ctrl+C doesn't truncate a write.
    context_updater = BackgroundContextUpdater(model, workspace) if model else None

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        yield
        if context_updater is None:
            return
        try:
            await asyncio.to_thread(context_updater.wait, 15.0)
        except Exception as exc:  # pragma: no cover - best-effort on shutdown
            logger.warning("CONTEXT.md update on shutdown failed: %s", exc)

    async def health(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "version": __version__})

    async def playground(request: Request) -> HTMLResponse:
        return HTMLResponse(_PLAYGROUND_HTML)

    async def handle_message(request: Request) -> JSONResponse:
        req, err = await _parse_request(request)
        if err is not None:
            return err
        try:
            cid = req.conversation_id
            if cid is not None:
                try:
                    validate_session_id(cid)
                except ValueError as exc:
                    logger.info("rejected conversation_id: %s", exc)
                    return JSONResponse(
                        {"error": "invalid conversation_id"}, status_code=400
                    )
            records, err_resp = _resolve_attachments(upload_store, cid, req.file_ids)
            if err_resp is not None:
                return err_resp
            try:
                prompt = build_agent_input(records, req.message)
            except UploadError as exc:
                return JSONResponse({"error": str(exc)}, status_code=400)
            conv_lock = await conv_locks.get(cid) if cid else None
            session_manager = (
                FileSessionManager(session_id=cid, storage_dir=sessions_dir)
                if cid
                else None
            )

            async def _run() -> JSONResponse:
                async with semaphore:
                    # Both endpoints use runner_session directly (not run_turn) to
                    # keep trigger(agent) inside the agent-alive scope — it must
                    # precede cleanup_mcp_clients (runner_session.__exit__),
                    # which is enforced structurally by this `with` block. See
                    # docs/superpowers/specs/2026-05-17-http-runner-migration-design.md.
                    with runner_session(
                        config,
                        workspace,
                        model,
                        session_manager=session_manager,
                        channel="api",
                        callback_handler=None,
                        scripted=True,
                    ) as ctx:
                        capture_path = (
                            next_capture_path(workspace.root, cid) if cid else None
                        )
                        start_time = datetime.now(timezone.utc)
                        try:
                            result = await asyncio.wait_for(
                                asyncio.to_thread(ctx.agent, prompt),
                                timeout=_TIMEOUT,
                            )
                        except asyncio.TimeoutError:
                            return JSONResponse(
                                {"error": "agent timed out"}, status_code=504
                            )
                        end_time = datetime.now(timezone.utc)
                        outcome = finalize(
                            ctx,
                            result,
                            goal=req.message,
                            source="api",
                            conversation_id=cid,
                            start_time=start_time,
                            end_time=end_time,
                            config=config,
                            capture_path=capture_path,
                            workspace_root=workspace.root,
                        )
                        if context_updater is not None:
                            try:
                                context_updater.trigger(ctx.agent)
                            except Exception as exc:
                                logger.debug("context update trigger failed: %s", exc)
                        # WorkspaceError here propagates to the outer except
                        # WorkspaceError in handle_message — same 500 shape.
                        workspace.write_trace(outcome.trace)
                        workspace.append_history(outcome.record)
                        return JSONResponse(
                            _build_response_payload(
                                outcome.result, req, config, outcome.trace.run_id
                            )
                        )

            if conv_lock is not None:
                async with conv_lock:
                    return await _run()
            return await _run()
        except WorkspaceError as exc:
            return JSONResponse({"error": f"workspace error: {exc}"}, status_code=500)
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)

    async def handle_message_stream(request: Request):
        req, err = await _parse_request(request)
        if err is not None:
            return err

        cid = req.conversation_id
        if cid is not None:
            try:
                validate_session_id(cid)
            except ValueError as exc:
                logger.info("rejected conversation_id (stream): %s", exc)
                return JSONResponse(
                    {"error": "invalid conversation_id"}, status_code=400
                )
        records, err_resp = _resolve_attachments(upload_store, cid, req.file_ids)
        if err_resp is not None:
            return err_resp
        try:
            prompt = build_agent_input(records, req.message)
        except UploadError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        conv_lock = await conv_locks.get(cid) if cid else None
        session_manager = (
            FileSessionManager(session_id=cid, storage_dir=sessions_dir)
            if cid
            else None
        )

        async def event_stream():
            try:

                async def _run():
                    async with semaphore:
                        # Both endpoints use runner_session directly (not
                        # run_turn) to keep trigger(agent) inside the
                        # agent-alive scope — it must precede
                        # cleanup_mcp_clients (runner_session.__exit__),
                        # which is enforced structurally by this `with`
                        # block. stream_async stays adapter-driven (the
                        # runner spec's documented streaming carve-out).
                        with runner_session(
                            config,
                            workspace,
                            model,
                            session_manager=session_manager,
                            channel="api",
                            callback_handler=None,
                            scripted=True,
                        ) as ctx:
                            capture_path = (
                                next_capture_path(workspace.root, cid) if cid else None
                            )
                            start_time = datetime.now(timezone.utc)
                            final_result: Any = None
                            try:
                                async with asyncio.timeout(_TIMEOUT):
                                    async for event in ctx.agent.stream_async(prompt):
                                        # stream_async is typed
                                        # AsyncIterator[Any]; isinstance fence
                                        # protects against future SDK changes.
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
                                # Backlog item 7: surface this via finalize_error
                                # + capture once a partial-record path exists.
                                yield _sse(
                                    "error",
                                    {"error": "agent did not return a result"},
                                )
                                return

                            end_time = datetime.now(timezone.utc)
                            outcome = finalize(
                                ctx,
                                final_result,
                                goal=req.message,
                                source="api",
                                conversation_id=cid,
                                start_time=start_time,
                                end_time=end_time,
                                config=config,
                                capture_path=capture_path,
                                workspace_root=workspace.root,
                            )
                            if context_updater is not None:
                                try:
                                    context_updater.trigger(ctx.agent)
                                except Exception as exc:
                                    logger.debug(
                                        "context update trigger failed: %s", exc
                                    )
                            try:
                                workspace.write_trace(outcome.trace)
                                workspace.append_history(outcome.record)
                            except WorkspaceError as exc:
                                # Inner catch is preserved on the streaming
                                # path so the SSE error event is emitted
                                # BEFORE the stream closes (ordering matters
                                # for clients parsing event boundaries).
                                yield _sse(
                                    "error",
                                    {"error": f"workspace error: {exc}"},
                                )
                                return

                            yield _sse(
                                "done",
                                _build_response_payload(
                                    outcome.result,
                                    req,
                                    config,
                                    outcome.trace.run_id,
                                ),
                            )

                if conv_lock is not None:
                    async with conv_lock:
                        async for evt in _run():
                            yield evt
                else:
                    async for evt in _run():
                        yield evt
            except Exception as exc:
                yield _sse("error", {"error": str(exc)})

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    async def handle_upload(request: Request) -> JSONResponse:
        if not _authenticate(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        cl = request.headers.get("content-length")
        if cl and int(cl) > MAX_UPLOAD_BYTES:
            return JSONResponse(
                {"error": f"file exceeds {MAX_UPLOAD_BYTES} byte cap"},
                status_code=413,
            )
        try:
            form = await request.form()
        except Exception:
            return JSONResponse({"error": "invalid multipart body"}, status_code=400)
        upload = form.get("file")
        if upload is None or not hasattr(upload, "filename"):
            return JSONResponse({"error": "missing 'file' field"}, status_code=400)
        cid = form.get("conversation_id")
        if cid is None or cid == "":
            cid = "tmp-" + secrets.token_hex(8)
        try:
            record = upload_store.save(str(cid), upload.filename or "", upload.file)
        except UploadError as exc:
            msg = str(exc)
            if "exceeds" in msg:
                status = 413
            elif "not allowed" in msg or "does not match" in msg:
                status = 415
            else:
                status = 400
            return JSONResponse({"error": msg}, status_code=status)
        except Exception as exc:
            return JSONResponse({"error": f"upload failed: {exc}"}, status_code=500)
        payload = UploadResponse(
            file_id=record.file_id,
            filename=record.filename,
            mime_type=record.mime_type,
            size_bytes=record.size_bytes,
            conversation_id=record.conversation_id,
        )
        return JSONResponse(payload.model_dump(), status_code=201)

    app = Starlette(
        routes=[
            Route("/health", health, methods=["GET"]),
            Route("/playground", playground, methods=["GET"]),
            Route("/message", handle_message, methods=["POST"]),
            Route("/message/stream", handle_message_stream, methods=["POST"]),
            Route("/upload", handle_upload, methods=["POST"]),
        ],
        lifespan=lifespan,
    )
    return CORSMiddleware(
        app,
        allow_origins=[cors_origin] if cors_origin else [],
        allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
        allow_methods=["*"],
        allow_headers=["*"],
    )
