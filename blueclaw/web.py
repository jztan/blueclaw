"""Local web server for trace visualization."""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from importlib.resources import files
from pathlib import Path

from starlette.applications import Starlette
from starlette.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    Response,
    StreamingResponse,
)
from starlette.routing import Route

from blueclaw.models import RunTrace, classify_error
from blueclaw.workspace import Workspace


def _count_trace_files(workspace: Workspace) -> int:
    """Count trace JSON files without parsing them."""
    if not workspace.traces_dir.exists():
        return 0
    return len(list(workspace.traces_dir.glob("*.json")))


_PREVIEW_MAX_CHARS = 200


def _compute_capture_preview(
    workspace_root: Path, capture_path: str | None
) -> tuple[str | None, bool]:
    """Read first line of capture_path/response.txt for inline preview.

    Returns (preview, captures_pruned):
      - (None, False) — no capture was associated with this trace
      - (None, True)  — capture_path was set but the file is missing (pruned)
      - ("...", False) — file exists; preview is the first line, truncated
                         to _PREVIEW_MAX_CHARS chars with '…' suffix if longer.
                         Empty string is valid (file exists but is empty, or
                         a permission/decode error occurred — UI shows empty
                         chip and the user can still click "view full").

    Never raises. Single open() per call, no separate exists() stat.
    Atomicity note: a concurrent read while the runner is mid-write may
    see partial content — accepted for a UI-only preview.

    Boundary note: a line whose length is EXACTLY _PREVIEW_MAX_CHARS is
    returned untruncated (no ellipsis). The `> _PREVIEW_MAX_CHARS` check
    is consistent — anything strictly longer is truncated to the same
    final width as the boundary case.
    """
    if capture_path is None:
        return (None, False)
    response_file = workspace_root / capture_path / "response.txt"
    try:
        with open(response_file, "rb") as f:
            raw = f.read(_PREVIEW_MAX_CHARS * 4)  # generous buffer for UTF-8
    except FileNotFoundError:
        return (None, True)
    except OSError:
        return ("", False)  # exists-but-unreadable is NOT pruned
    text = raw.decode("utf-8", errors="replace")
    first_line = text.split("\n", 1)[0]
    if len(first_line) > _PREVIEW_MAX_CHARS:
        first_line = first_line[: _PREVIEW_MAX_CHARS - 1] + "…"
    return (first_line, False)


def _serialize_trace_summary(
    t: RunTrace, *, workspace_root: Path | None = None
) -> dict:
    """Convert a RunTrace to a lightweight summary dict for the list view."""
    elapsed = (t.end_time - t.start_time).total_seconds()
    summary = {
        "run_id": t.run_id,
        "goal": t.goal,
        "model_id": t.model_id,
        "status": t.status,
        "steps": len(t.steps),
        "total_tokens": t.total_tokens,
        "total_cost": t.total_cost,
        "duration_s": round(elapsed, 1),
        "start_time": t.start_time.isoformat(),
        "context_strategy": t.context_strategy,
        "context_masked_chars": t.context_masked_chars,
        "source": t.source,
        "conversation_id": t.conversation_id,
        "capture_path": t.capture_path,
    }
    if workspace_root is not None and t.capture_path is not None:
        preview, pruned = _compute_capture_preview(workspace_root, t.capture_path)
        if pruned:
            summary["captures_pruned"] = True
        elif preview is not None:
            summary["capture_preview"] = preview
    return summary


def _serialize_turn_summary(trace: RunTrace, workspace_root: Path) -> dict:
    """Convert a RunTrace to a per-turn summary dict for the conversation view."""
    elapsed = (trace.end_time - trace.start_time).total_seconds()
    # Derive turn_n from capture_path "turn-NNN" segment; caller fills in fallback.
    turn_n: int | None = None
    if trace.capture_path is not None:
        # e.g. ".blueclaw/conversations/A/turns/turn-001"
        segment = Path(trace.capture_path).name
        if segment.startswith("turn-") and segment[5:].isdigit():
            turn_n = int(segment[5:])
    # Check events.jsonl existence
    has_events = False
    if trace.capture_path is not None:
        has_events = (workspace_root / trace.capture_path / "events.jsonl").exists()
    return {
        "turn_n": turn_n,  # may be None; caller replaces with index+1 if so
        "run_id": trace.run_id,
        "goal": trace.goal,
        "status": trace.status,
        "model_id": trace.model_id,
        "start_time": trace.start_time.isoformat(),
        "duration_s": round(elapsed, 1),
        "tokens": trace.total_tokens,
        "cost": trace.total_cost,
        "capture_path": trace.capture_path,
        "has_events_jsonl": has_events,
    }


def _aggregate_conversations(traces: list[RunTrace]) -> dict[str, dict]:
    """Group a flat trace list by conversation_id.

    Returns a mapping of {cid: aggregate_dict}. Traces without a
    conversation_id are skipped. Cost is summed as float (None → 0.0,
    counted in turns_with_unknown_cost).
    """
    aggs: dict[str, dict] = {}
    for t in traces:
        cid = t.conversation_id
        if not cid:
            continue
        if cid not in aggs:
            aggs[cid] = {
                "conversation_id": cid,
                "source": t.source,
                "turn_count": 0,
                "first_turn_at": t.start_time,
                "last_turn_at": t.start_time,
                "total_tokens": 0,
                "total_cost": 0.0,
                "turns_with_unknown_cost": 0,
                "model_ids": [],
                "status_counts": {},
            }
        agg = aggs[cid]
        agg["turn_count"] += 1
        if t.start_time < agg["first_turn_at"]:
            agg["first_turn_at"] = t.start_time
        if t.start_time > agg["last_turn_at"]:
            agg["last_turn_at"] = t.start_time
            # keep source from the most recent turn
            agg["source"] = t.source
        agg["total_tokens"] += t.total_tokens
        if t.total_cost is None:
            agg["turns_with_unknown_cost"] += 1
        else:
            agg["total_cost"] += t.total_cost
        if t.model_id and t.model_id not in agg["model_ids"]:
            agg["model_ids"].append(t.model_id)
        status = t.status or "unknown"
        agg["status_counts"][status] = agg["status_counts"].get(status, 0) + 1
    return aggs


def compute_stats(traces: list[RunTrace]) -> dict:
    """Compute aggregate stats from a list of traces.

    Used by both `trace stats` CLI command and GET /api/stats endpoint.
    """
    if not traces:
        return {
            "total_runs": 0,
            "total_steps": 0,
            "avg_steps_per_run": 0,
            "avg_tokens_per_run": 0,
            "avg_cost_per_run": None,
            "total_cost": None,
            "median_duration_s": 0,
            "p95_duration_s": 0,
            "avg_tool_time_pct": 0,
            "top_tools": [],
            "errors": [],
            "error_rate": 0,
            "failed_steps": 0,
            "runs_with_failures": 0,
            "context": {
                "runs_with_masking": 0,
                "avg_masked_chars": 0,
                "strategies": {},
            },
            "daily_costs": [],
        }

    total_runs = len(traces)
    all_steps = [s for t in traces for s in t.steps]
    total_steps = len(all_steps)
    failed_steps = [s for s in all_steps if s.status == "error"]

    avg_steps = total_steps / total_runs
    avg_tokens = sum(t.total_tokens for t in traces) / total_runs

    costs = [t.total_cost for t in traces if t.total_cost is not None]
    avg_cost = sum(costs) / len(costs) if costs else None
    total_cost = sum(costs) if costs else None

    # Timing
    wall_times = []
    tool_times = []
    for t in traces:
        wall_ms = int((t.end_time - t.start_time).total_seconds() * 1000)
        tool_ms = sum(s.duration_ms for s in t.steps)
        wall_times.append(wall_ms)
        tool_times.append(tool_ms)

    avg_wall = sum(wall_times) / total_runs
    avg_tool = sum(tool_times) / total_runs
    durations_sorted = sorted(wall_times)
    median = durations_sorted[len(durations_sorted) // 2]
    p95 = durations_sorted[int(len(durations_sorted) * 0.95)]
    tool_pct = (avg_tool / avg_wall * 100) if avg_wall > 0 else 0

    # Tool frequency
    tool_counts = Counter(s.tool_name for s in all_steps)
    top_tools = [
        {
            "name": name,
            "count": count,
            "pct": round(count / total_steps * 100),
        }
        for name, count in tool_counts.most_common(10)
    ]

    # Error classification
    errors_list = []
    runs_with_failures = 0
    error_rate = 0.0
    if failed_steps:
        failure_counts = Counter(classify_error(s.error) for s in failed_steps)
        error_rate = round(len(failed_steps) / total_steps * 100, 1)
        runs_with_failures = len(
            {t.run_id for t in traces for s in t.steps if s.status == "error"}
        )
        errors_list = [
            {
                "category": cat,
                "count": cnt,
                "pct": round(cnt / len(failed_steps) * 100),
            }
            for cat, cnt in failure_counts.most_common()
        ]

    # Context management
    traces_with_ctx = [t for t in traces if t.context_masked_chars is not None]
    strategies = (
        Counter(t.context_strategy for t in traces_with_ctx if t.context_strategy)
        if traces_with_ctx
        else Counter()
    )
    ctx_stats = {
        "runs_with_masking": len(traces_with_ctx),
        "avg_masked_chars": (
            round(
                sum(t.context_masked_chars for t in traces_with_ctx)
                / len(traces_with_ctx)
            )
            if traces_with_ctx
            else 0
        ),
        "strategies": dict(strategies),
    }

    # Daily costs
    daily: dict[str, float] = {}
    for t in traces:
        if t.total_cost is not None:
            day = t.start_time.strftime("%Y-%m-%d")
            daily[day] = daily.get(day, 0) + t.total_cost
    daily_costs = [{"date": d, "cost": round(c, 4)} for d, c in sorted(daily.items())]

    return {
        "total_runs": total_runs,
        "total_steps": total_steps,
        "avg_steps_per_run": round(avg_steps, 1),
        "avg_tokens_per_run": round(avg_tokens),
        "avg_cost_per_run": round(avg_cost, 4) if avg_cost else None,
        "total_cost": round(total_cost, 2) if total_cost else None,
        "median_duration_s": round(median / 1000, 1),
        "p95_duration_s": round(p95 / 1000, 1),
        "avg_tool_time_pct": round(tool_pct, 1),
        "top_tools": top_tools,
        "errors": errors_list,
        "error_rate": error_rate,
        "failed_steps": len(failed_steps),
        "runs_with_failures": runs_with_failures,
        "context": ctx_stats,
        "daily_costs": daily_costs,
    }


def _read_static(name: str) -> bytes:
    """Read a static file, with fallback for editable installs."""
    try:
        resource = files("blueclaw").joinpath("static", name)
        return resource.read_bytes()
    except Exception:
        return (Path(__file__).parent / "static" / name).read_bytes()


def _read_static_text(name: str) -> str:
    """Read a static text file, with fallback for editable installs."""
    try:
        resource = files("blueclaw").joinpath("static", name)
        return resource.read_text(encoding="utf-8")
    except Exception:
        return (Path(__file__).parent / "static" / name).read_text(encoding="utf-8")


def _parse_since(request) -> datetime | None:
    """Extract ?since=<days> as an absolute UTC datetime, or None."""
    raw = request.query_params.get("since")
    if not raw:
        return None
    try:
        return datetime.now(timezone.utc) - timedelta(days=int(raw))
    except ValueError:
        return None


def create_app(
    workspaces: "list[tuple[str, Workspace]] | Workspace",
) -> Starlette:
    """Create the Starlette app with trace API routes.

    Accepts either a single Workspace (back-compat) or a list of
    (key, Workspace) tuples. Endpoints honor ?workspace=<key>, with
    a special "all" key that unions every registered workspace.
    """
    if isinstance(workspaces, Workspace):
        workspaces = [("workspace", workspaces)]
    ws_map = dict(workspaces)
    keys_in_order = [k for k, _ in workspaces]

    def _select(key: str | None) -> "list[tuple[str, Workspace]] | None":
        if key is None:
            return [(keys_in_order[0], ws_map[keys_in_order[0]])]
        if key == "all":
            return list(workspaces)
        if key not in ws_map:
            return None
        return [(key, ws_map[key])]

    async def index(request):
        from blueclaw import __version__

        html = _read_static_text("dashboard.html")
        html = html.replace("{{VERSION}}", __version__)
        return HTMLResponse(html)

    async def crab_png(request):
        data = _read_static("blueclaw-crab.png")
        return Response(data, media_type="image/png")

    async def list_workspaces(request):
        return JSONResponse([{"key": k, "label": k} for k in keys_in_order])

    async def list_traces(request):
        sel = _select(request.query_params.get("workspace"))
        if sel is None:
            return JSONResponse({"error": "unknown workspace"}, status_code=404)
        try:
            limit = int(request.query_params.get("limit", 50))
        except ValueError:
            limit = 50
        since = _parse_since(request)
        model = request.query_params.get("model")
        summaries: list = []
        total = 0
        for key, ws in sel:
            total += _count_trace_files(ws)
            traces = ws.list_traces(limit=limit, since=since)
            if model:
                traces = [t for t in traces if t.model_id == model]
            for t in traces:
                summary = _serialize_trace_summary(t, workspace_root=ws.root)
                summary["_source"] = key
                summaries.append(summary)
        summaries.sort(key=lambda r: r["start_time"], reverse=True)
        return JSONResponse({"traces": summaries[:limit], "total": total})

    async def get_trace(request):
        run_id = request.path_params["run_id"]
        if not re.match(r"^\d{8}-\d{6}(-[0-9a-f]{4})?$", run_id):
            return JSONResponse({"error": "invalid run_id"}, status_code=400)
        sel = _select(request.query_params.get("workspace"))
        if sel is None:
            return JSONResponse({"error": "unknown workspace"}, status_code=404)
        matches: list = []
        for key, ws in sel:
            t = ws.read_trace(run_id)
            if t is not None:
                matches.append((key, t))
        if not matches:
            return JSONResponse({"error": "not found"}, status_code=404)
        if len(matches) > 1:
            return JSONResponse(
                {
                    "error": "ambiguous",
                    "candidates": [k for k, _ in matches],
                },
                status_code=409,
            )
        key, t = matches[0]
        obj = t.model_dump(mode="json")
        obj["_source"] = key
        return JSONResponse(obj)

    async def get_stats(request):
        sel = _select(request.query_params.get("workspace"))
        if sel is None:
            return JSONResponse({"error": "unknown workspace"}, status_code=404)
        since = _parse_since(request)
        model = request.query_params.get("model")
        union: list = []
        by_source: dict = {}
        for key, ws in sel:
            rows = ws.list_traces(limit=500, since=since)
            if model:
                rows = [t for t in rows if t.model_id == model]
            union.extend(rows)
            if len(sel) > 1:
                by_source[key] = compute_stats(rows)
        payload = compute_stats(union)
        if by_source:
            payload["by_source"] = by_source
        return JSONResponse(payload)

    async def get_turn_response(request):
        from blueclaw.runner import validate_session_id

        cid = request.path_params["cid"]
        n_raw = request.path_params["n"]

        # Order of checks (load-bearing for no-echo):
        # 1. cid validation — generic 400, no echo
        try:
            validate_session_id(cid)
        except ValueError:
            return JSONResponse({"error": "invalid cid"}, status_code=400)
        # 2. <n> regex (1..99999, no leading zeros) — generic 400, no echo
        # ^[1-9]\d{0,4}$ → 1..99999, no leading zeros, no n=0. Caps the
        # zero-padded string length and gives one canonical URL form per
        # turn (so /turn/cid/5/... and /turn/cid/005/... aren't aliases).
        if not re.match(r"^[1-9]\d{0,4}$", n_raw):
            return JSONResponse({"error": "invalid turn number"}, status_code=400)
        n = int(n_raw)

        # 3. workspace selection
        sel = _select(request.query_params.get("workspace"))
        if sel is None:
            return JSONResponse({"error": "unknown workspace"}, status_code=404)

        # 4. file existence — echo of expected_path is safe now (cid validated)
        rel = f".blueclaw/conversations/{cid}/turns/turn-{n:03d}/response.txt"
        for _key, ws in sel:
            f = ws.root / rel
            if f.exists():
                try:
                    return PlainTextResponse(
                        f.read_text(encoding="utf-8", errors="replace")
                    )
                except OSError:
                    break
        return JSONResponse(
            {
                "error": "capture not found",
                "expected_path": rel,
                "hint": (
                    "may have been pruned (no retention policy is enforced) "
                    "or workspace root may have moved"
                ),
            },
            status_code=404,
        )

    async def get_turn_messages(request):
        from blueclaw.runner import validate_session_id

        cid = request.path_params["cid"]
        n_raw = request.path_params["n"]

        try:
            validate_session_id(cid)
        except ValueError:
            return JSONResponse({"error": "invalid cid"}, status_code=400)
        if not re.match(r"^[1-9]\d{0,4}$", n_raw):
            return JSONResponse({"error": "invalid turn number"}, status_code=400)
        n = int(n_raw)

        sel = _select(request.query_params.get("workspace"))
        if sel is None:
            return JSONResponse({"error": "unknown workspace"}, status_code=404)

        rel = f".blueclaw/conversations/{cid}/turns/turn-{n:03d}/messages.json"
        for _key, ws in sel:
            f = ws.root / rel
            if f.exists():
                try:
                    return Response(
                        f.read_bytes(),
                        media_type="application/json",
                    )
                except OSError:
                    break
        return JSONResponse(
            {
                "error": "capture not found",
                "expected_path": rel,
                "hint": (
                    "may have been pruned (no retention policy is enforced) "
                    "or workspace root may have moved"
                ),
            },
            status_code=404,
        )

    async def stream_events(request):
        from blueclaw.runner import validate_session_id

        cid = request.path_params["cid"]
        n_raw = request.path_params["n"]

        try:
            validate_session_id(cid)
        except ValueError:
            return JSONResponse({"error": "invalid cid"}, status_code=400)
        if not re.match(r"^[1-9]\d{0,4}$", n_raw):
            return JSONResponse({"error": "invalid turn number"}, status_code=400)
        n = int(n_raw)

        sel = _select(request.query_params.get("workspace"))
        if sel is None:
            return JSONResponse({"error": "unknown workspace"}, status_code=404)

        # expected_path is safe to echo: cid and n are validated above.
        rel = f".blueclaw/conversations/{cid}/turns/turn-{n:03d}/events.jsonl"
        for _key, ws in sel:
            f = ws.root / rel
            if f.exists():

                def file_iter(path=f):
                    with open(path, "rb") as fh:
                        while True:
                            chunk = fh.read(8192)
                            if not chunk:
                                return
                            yield chunk

                return StreamingResponse(
                    file_iter(),
                    media_type="application/x-ndjson",
                    headers={"Content-Disposition": "inline"},
                )
        return JSONResponse(
            {"error": "not found", "expected_path": rel},
            status_code=404,
        )

    async def get_conversation(request):
        from blueclaw.runner import validate_session_id

        cid = request.path_params["cid"]
        try:
            validate_session_id(cid)
        except ValueError:
            return JSONResponse({"error": "invalid conversation_id"}, status_code=400)

        sel = _select(request.query_params.get("workspace"))
        if sel is None:
            return JSONResponse({"error": "unknown workspace"}, status_code=404)

        # Collect all traces matching cid across selected workspaces.
        matching_traces: list[tuple[str, RunTrace, Path]] = []
        for ws_key, ws in sel:
            traces = ws.list_traces(limit=500)
            for t in traces:
                if t.conversation_id == cid:
                    matching_traces.append((ws_key, t, ws.root))

        if not matching_traces:
            return JSONResponse({"error": "not found"}, status_code=404)

        # Build aggregate via reused helper.
        all_traces = [t for _, t, _ in matching_traces]
        agg = _aggregate_conversations(all_traces).get(cid)
        if agg is None:
            return JSONResponse({"error": "not found"}, status_code=404)

        # Serialize turn list sorted by start_time ascending.
        turn_dicts = []
        for _, t, ws_root in matching_traces:
            turn_dicts.append(_serialize_turn_summary(t, ws_root))
        turn_dicts.sort(key=lambda d: d["start_time"])

        # Fill in turn_n for any turns that could not derive it from capture_path.
        for i, td in enumerate(turn_dicts):
            if td["turn_n"] is None:
                td["turn_n"] = i + 1

        return JSONResponse(
            {
                "conversation_id": agg["conversation_id"],
                "source": agg["source"],
                "turn_count": agg["turn_count"],
                "first_turn_at": agg["first_turn_at"].isoformat(),
                "last_turn_at": agg["last_turn_at"].isoformat(),
                "total_tokens": agg["total_tokens"],
                "total_cost": round(agg["total_cost"], 6),
                "turns_with_unknown_cost": agg["turns_with_unknown_cost"],
                "model_ids": sorted(agg["model_ids"]),
                "status_counts": agg["status_counts"],
                "turns": turn_dicts,
            }
        )

    async def list_conversations(request):
        sel = _select(request.query_params.get("workspace"))
        if sel is None:
            return JSONResponse({"error": "unknown workspace"}, status_code=404)
        try:
            limit = int(request.query_params.get("limit", 50))
        except ValueError:
            limit = 50
        since = _parse_since(request)

        # Collect aggregates per workspace, merging by cid (prefer latest
        # last_turn_at when the same cid appears in multiple workspaces).
        merged: dict[str, dict] = {}
        for ws_key, ws in sel:
            traces = ws.list_traces(limit=500, since=since)
            per_ws = _aggregate_conversations(traces)
            for cid, agg in per_ws.items():
                if cid not in merged:
                    agg["_source"] = ws_key
                    merged[cid] = agg
                else:
                    existing = merged[cid]
                    if agg["last_turn_at"] > existing["last_turn_at"]:
                        agg["_source"] = ws_key
                        merged[cid] = agg
                    # else keep existing (it has the later last_turn_at)

        # Serialize datetime fields, sort by last_turn_at desc, apply limit.
        conversations = []
        for agg in merged.values():
            conversations.append(
                {
                    "conversation_id": agg["conversation_id"],
                    "source": agg["source"],
                    "turn_count": agg["turn_count"],
                    "first_turn_at": agg["first_turn_at"].isoformat(),
                    "last_turn_at": agg["last_turn_at"].isoformat(),
                    "total_tokens": agg["total_tokens"],
                    "total_cost": round(agg["total_cost"], 6),
                    "turns_with_unknown_cost": agg["turns_with_unknown_cost"],
                    "model_ids": sorted(agg["model_ids"]),
                    "status_counts": agg["status_counts"],
                    "_source": agg["_source"],
                }
            )
        conversations.sort(key=lambda c: c["last_turn_at"], reverse=True)
        total = len(conversations)
        return JSONResponse({"conversations": conversations[:limit], "total": total})

    return Starlette(
        routes=[
            Route("/", index),
            Route("/blueclaw-crab.png", crab_png),
            Route("/api/workspaces", list_workspaces),
            Route("/api/traces", list_traces),
            Route("/api/traces/{run_id}", get_trace),
            Route("/api/stats", get_stats),
            Route("/api/conversations", list_conversations),
            Route("/api/conversations/{cid}", get_conversation),
            Route("/api/turns/{cid}/{n}/response", get_turn_response),
            Route("/api/turns/{cid}/{n}/messages", get_turn_messages),
            Route(
                "/api/conversations/{cid}/turns/{n}/events",
                stream_events,
            ),
        ]
    )
