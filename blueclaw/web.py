"""Local web server for trace visualization."""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from importlib.resources import files
from pathlib import Path

from starlette.applications import Starlette
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route

from blueclaw.models import RunTrace, classify_error
from blueclaw.workspace import Workspace


def _count_trace_files(workspace: Workspace) -> int:
    """Count trace JSON files without parsing them."""
    if not workspace.traces_dir.exists():
        return 0
    return len(list(workspace.traces_dir.glob("*.json")))


def _serialize_trace_summary(t: RunTrace) -> dict:
    """Convert a RunTrace to a lightweight summary dict for the list view."""
    elapsed = (t.end_time - t.start_time).total_seconds()
    return {
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
    }


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


def create_app(workspace: Workspace) -> Starlette:
    """Create the Starlette app with trace API routes."""

    async def index(request):
        from blueclaw import __version__

        html = _read_static_text("dashboard.html")
        html = html.replace("{{VERSION}}", __version__)
        return HTMLResponse(html)

    async def crab_png(request):
        data = _read_static("blueclaw-crab.png")
        return Response(data, media_type="image/png")

    async def list_traces(request):
        try:
            limit = int(request.query_params.get("limit", 50))
        except ValueError:
            limit = 50
        since_days = request.query_params.get("since")
        model = request.query_params.get("model")
        since = None
        if since_days:
            try:
                since = datetime.now(timezone.utc) - timedelta(days=int(since_days))
            except ValueError:
                pass
        traces = workspace.list_traces(limit=limit, since=since)
        if model:
            traces = [t for t in traces if t.model_id == model]
        total = _count_trace_files(workspace)
        return JSONResponse(
            {
                "traces": [_serialize_trace_summary(t) for t in traces],
                "total": total,
            }
        )

    async def get_trace(request):
        run_id = request.path_params["run_id"]
        if not re.match(r"^\d{8}-\d{6}$", run_id):
            return JSONResponse({"error": "invalid run_id"}, status_code=400)
        trace = workspace.read_trace(run_id)
        if not trace:
            return JSONResponse({"error": "not found"}, status_code=404)
        return JSONResponse(trace.model_dump(mode="json"))

    async def get_stats(request):
        since_days = request.query_params.get("since")
        model = request.query_params.get("model")
        since = None
        if since_days:
            try:
                since = datetime.now(timezone.utc) - timedelta(days=int(since_days))
            except ValueError:
                pass
        traces = workspace.list_traces(limit=500, since=since)
        if model:
            traces = [t for t in traces if t.model_id == model]
        return JSONResponse(compute_stats(traces))

    return Starlette(
        routes=[
            Route("/", index),
            Route("/blueclaw-crab.png", crab_png),
            Route("/api/traces", list_traces),
            Route("/api/traces/{run_id}", get_trace),
            Route("/api/stats", get_stats),
        ]
    )
