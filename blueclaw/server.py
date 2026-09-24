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
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
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
)
from blueclaw.uploads import (
    MAX_UPLOAD_BYTES,
    UploadError,
    UploadStore,
    build_agent_input,
)
from blueclaw.session import (
    BackgroundContextUpdater,
)
from blueclaw.runner import (
    bus_for_turn,
    finalize,
    finalize_error,
    finalize_unstarted,
    next_capture_path,
    runner_session,
    validate_session_id,
)
from blueclaw.requests import ActiveRequest, RequestRegistry
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
        # identifier failures that can contain sensitive input, so collapse
        # to a generic message instead of forwarding the validation detail.
        for err in exc.errors():
            for field in ("conversation_id", "request_id"):
                if field not in err.get("loc", ()):
                    continue
                return None, JSONResponse(
                    {"error": f"invalid {field}"}, status_code=400
                )
        return None, JSONResponse({"error": str(exc)}, status_code=400)
    except (json.JSONDecodeError, TypeError) as exc:
        return None, JSONResponse({"error": str(exc)}, status_code=400)
    return req, None


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
    requests = RequestRegistry()
    upload_store = UploadStore(workspace.root)

    # Per-turn CONTEXT.md updater. trigger() is no-op if a previous update is
    # still running, so concurrent turns across conversations can't race and
    # never queue more than one writer at a time. The shutdown handler waits
    # on the last in-flight thread so Ctrl+C doesn't truncate a write.
    context_updater = BackgroundContextUpdater(model, workspace) if model else None

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        yield
        await requests.shutdown()
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

    async def _prepare_message(request: Request):
        req, err = await _parse_request(request)
        if err is not None:
            return None, None, err
        cid = req.conversation_id
        if cid is not None:
            try:
                validate_session_id(cid)
            except ValueError:
                return (
                    None,
                    None,
                    JSONResponse({"error": "invalid conversation_id"}, status_code=400),
                )
        records, err = _resolve_attachments(upload_store, cid, req.file_ids)
        if err is not None:
            return None, None, err
        try:
            prompt = build_agent_input(records, req.message)
        except UploadError as exc:
            return None, None, JSONResponse({"error": str(exc)}, status_code=400)
        return req, prompt, None

    async def _acquire_or_stop(lock, active: ActiveRequest) -> bool:
        while not active.cancellation.event.is_set():
            try:
                await asyncio.wait_for(lock.acquire(), timeout=0.05)
                return True
            except asyncio.TimeoutError:
                continue
        return False

    def _payload(outcome, req, request_id: str) -> dict:
        trace = outcome.trace
        return MessageResponse(
            reply=outcome.response_text,
            run_id=trace.run_id,
            conversation_id=req.conversation_id,
            tokens=trace.total_tokens,
            cost=trace.total_cost,
            status=trace.status,
            termination_reason=trace.termination_reason,
            original_termination_reason=trace.original_termination_reason,
            usage_complete=trace.usage_complete,
        ).model_dump() | {"request_id": request_id}

    async def _run_request(req, prompt, active: ActiveRequest):
        cid = req.conversation_id
        start_time = datetime.now(timezone.utc)
        conv_lock = await conv_locks.get(cid) if cid else None
        held_lock = False
        held_slot = False
        try:
            if conv_lock is not None:
                held_lock = await _acquire_or_stop(conv_lock, active)
            if (
                conv_lock is None or held_lock
            ) and not active.cancellation.event.is_set():
                held_slot = await _acquire_or_stop(semaphore, active)
            if active.cancellation.event.is_set() or not held_slot:
                reason = active.cancellation.begin_finalization() or "user"
                capture = next_capture_path(
                    workspace.root, "request-" + active.request_id
                )
                outcome = finalize_unstarted(
                    goal=req.message,
                    source="api",
                    conversation_id=cid,
                    start_time=start_time,
                    end_time=datetime.now(timezone.utc),
                    config=config,
                    capture_path=capture,
                    workspace_root=workspace.root,
                    termination_reason=reason,
                )
                workspace.write_trace(outcome.trace)
                workspace.append_history(outcome.record)
                return outcome

            active.admitted = True
            capture = next_capture_path(
                workspace.root, cid or "request-" + active.request_id
            )
            session_manager = (
                FileSessionManager(
                    session_id=cid,
                    storage_dir=str(workspace.conversation_dir(cid)),
                )
                if cid
                else None
            )
            with runner_session(
                config,
                workspace,
                model,
                session_manager=session_manager,
                channel="api",
                callback_handler=None,
                scripted=True,
                cancellation=active.cancellation,
            ) as ctx:
                with bus_for_turn(ctx.observer, capture, cid=cid) as bus:
                    chunks = []
                    result = None
                    error = None

                    async def timeout_after():
                        await asyncio.sleep(_TIMEOUT)
                        active.cancellation.request_stop("timeout")

                    timer = asyncio.create_task(timeout_after())
                    try:
                        async for event in ctx.agent.stream_async(prompt):
                            if not isinstance(event, dict):
                                continue
                            chunk = event.get("data")
                            if chunk:
                                chunks.append(chunk)
                                if active.streaming:
                                    await active.publish(
                                        {"type": "delta", "text": chunk}
                                    )
                            if event.get("result") is not None:
                                result = event["result"]
                    except Exception as exc:
                        error = exc
                    finally:
                        timer.cancel()
                        await asyncio.gather(timer, return_exceptions=True)

                    reason = active.cancellation.begin_finalization()
                    if result is None and error is None:
                        error = RuntimeError("agent did not return a result")
                    if result is not None and reason is None and error is None:
                        if context_updater is not None:
                            try:
                                context_updater.trigger(ctx.agent)
                            except Exception as exc:
                                logger.debug("context update trigger failed: %s", exc)
                    await asyncio.to_thread(ctx.close)
                    end_time = datetime.now(timezone.utc)
                    partial = "".join(chunks)
                    if error is not None:
                        outcome = finalize_error(
                            ctx,
                            error,
                            goal=req.message,
                            source="api",
                            conversation_id=cid,
                            start_time=start_time,
                            end_time=end_time,
                            config=config,
                            capture_path=capture,
                            workspace_root=workspace.root,
                            response_text=partial,
                        )
                    else:
                        status = None
                        if reason == "timeout":
                            status = "error"
                        elif reason is not None:
                            status = "cancelled"
                        outcome = finalize(
                            ctx,
                            result,
                            goal=req.message,
                            source="api",
                            conversation_id=cid,
                            start_time=start_time,
                            end_time=end_time,
                            config=config,
                            capture_path=capture,
                            workspace_root=workspace.root,
                            response_text=partial if reason is not None else None,
                            status=status,
                            termination_reason=reason,
                            usage_complete=(reason is None),
                        )
                    workspace.write_trace(outcome.trace)
                    workspace.append_history(outcome.record)
                    if bus is not None:
                        bus.emit(
                            {
                                "type": "run.terminal",
                                "status": outcome.trace.status,
                                "run_id": outcome.trace.run_id,
                            }
                        )
                    return outcome
        finally:
            if held_slot:
                semaphore.release()
            if held_lock:
                conv_lock.release()
            requests.finish(active.request_id)

    def _start_request(req, prompt, streaming: bool):
        request_id = req.request_id or secrets.token_hex(16)
        try:
            active = requests.register(request_id)
        except ValueError:
            return None, JSONResponse(
                {"error": "duplicate active request_id"}, status_code=409
            )
        active.streaming = streaming
        active.task = asyncio.create_task(_run_request(req, prompt, active))
        return active, None

    async def cancel_request(request: Request) -> JSONResponse:
        if not _authenticate(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        request_id = request.path_params["request_id"]
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", request_id):
            return JSONResponse({"error": "invalid request_id"}, status_code=400)
        active = requests.get(request_id)
        if active is None:
            return JSONResponse({"error": "request not active"}, status_code=404)
        if not active.cancellation.request_stop("user"):
            return JSONResponse({"error": "already_finishing"}, status_code=409)
        return JSONResponse(
            {"request_id": request_id, "status": "stopping"}, status_code=202
        )

    async def handle_message(request: Request) -> JSONResponse:
        req, prompt, err = await _prepare_message(request)
        if err is not None:
            return err
        active, err = _start_request(req, prompt, streaming=False)
        if err is not None:
            return err
        request_id = active.request_id

        async def watch_disconnect():
            while not active.task.done():
                if await request.is_disconnected():
                    active.cancellation.request_stop("disconnect")
                    return
                await asyncio.sleep(0.1)

        watcher = asyncio.create_task(watch_disconnect())
        try:
            outcome = await asyncio.shield(active.task)
        except asyncio.CancelledError:
            active.cancellation.request_stop("disconnect")
            await asyncio.shield(active.task)
            raise
        except Exception as exc:
            message = (
                f"workspace error: {exc}"
                if isinstance(exc, WorkspaceError)
                else str(exc)
            )
            return JSONResponse(
                {"error": message, "request_id": request_id},
                status_code=500,
                headers={"X-Blueclaw-Request-ID": request_id},
            )
        finally:
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)
        payload = _payload(outcome, req, request_id)
        if outcome.trace.termination_reason == "timeout":
            code = 504
            payload["error"] = "agent timed out"
        elif outcome.trace.status == "error":
            code = 500
            payload["error"] = str(outcome.error or "agent run failed")
        else:
            code = 200
        return JSONResponse(
            payload,
            status_code=code,
            headers={"X-Blueclaw-Request-ID": request_id},
        )

    async def handle_message_stream(request: Request):
        req, prompt, err = await _prepare_message(request)
        if err is not None:
            return err
        active, err = _start_request(req, prompt, streaming=True)
        if err is not None:
            return err
        request_id = active.request_id

        async def events():
            yield _sse("started", {"request_id": request_id})
            try:
                while not active.task.done() or not active.queue.empty():
                    try:
                        event = await asyncio.wait_for(active.queue.get(), 0.05)
                    except asyncio.TimeoutError:
                        continue
                    yield _sse(event["type"], {"text": event["text"]})
                outcome = await asyncio.shield(active.task)
                payload = _payload(outcome, req, request_id)
                if outcome.trace.status == "cancelled":
                    yield _sse("stopped", payload)
                elif outcome.trace.status == "error":
                    payload["error"] = (
                        "agent timed out"
                        if outcome.trace.termination_reason == "timeout"
                        else str(outcome.error or "agent run failed")
                    )
                    yield _sse("error", payload)
                else:
                    yield _sse("done", payload)
            except Exception as exc:
                message = (
                    f"workspace error: {exc}"
                    if isinstance(exc, WorkspaceError)
                    else str(exc)
                )
                yield _sse("error", {"error": message, "request_id": request_id})
            finally:
                active.detached.set()
                if not active.task.done():
                    active.cancellation.request_stop("disconnect")

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"X-Blueclaw-Request-ID": request_id},
        )

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
            Route("/requests/{request_id}/cancel", cancel_request, methods=["POST"]),
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
        expose_headers=["X-Blueclaw-Request-ID"],
    )
