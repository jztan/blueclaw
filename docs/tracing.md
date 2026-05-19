# Tracing & Observability

Every agent run produces a structured JSON trace in `.blueclaw/traces/`. Nine CLI commands let you inspect runs after the fact — no dashboards, no external services, no setup.

## Commands

| Command | Use case |
|---|---|
| `trace list` | Find a run ID to inspect |
| `trace show <id>` | Detailed step table with timing |
| `trace graph <id>` | Quick tree view of tool sequence |
| `trace timeline <id>` | Find bottlenecks — where does time go? |
| `trace explain <id>` | LLM explains what happened and why |
| `trace diff <id1> <id2>` | Compare two runs (A/B test prompts) |
| `trace replay <id>` | Step-through debugger for tool calls |
| `trace replay <id> --stub-tools` | Re-run with recorded outputs, compare tool sequence |
| `trace stats` | Aggregate performance across all runs |
| `trace ui` | Browser dashboard with charts and waterfall |
| `trace purge` | Delete old traces (default: 30 days) |

## See what happened: `trace graph`

```
$ blueclaw trace graph 20260315-054426

search for Python 3.13 new features
├── web_search (1ms) ✓  query: Python 3.13 new features
├── web_search (1ms) ✓  query: Python 3.13 new features list 2024
└── http_request (366ms) ✓  url: https://docs.python.org/3.13/whatsnew/3.13.html
```

## Find the bottleneck: `trace timeline`

```
$ blueclaw trace timeline 20260315-054426

Goal: search for Python 3.13 new features
Model: claude-sonnet-4-6 · 3 steps · 1840 tokens · $0.0073

 #  Tool          Start    Duration  Cumulative  Bar
 1  web_search      +0ms      1ms         1ms    █
 2  web_search    +120ms      1ms         2ms    █
 3  http_request  +250ms    366ms       368ms    ██████████████████████

Tool time: 368ms · Wall time: 4100ms · Overhead: 91%
```

## Understand why: `trace explain`

```
$ blueclaw trace explain 20260315-054426

The agent searched for Python 3.13 features, found the results too generic,
refined its query to include "list 2024", then fetched the official changelog
from docs.python.org. The two-step search pattern suggests the first results
didn't contain enough detail...

Post-hoc explanation · not the agent's actual reasoning
```

## Compare two runs: `trace diff`

```
$ blueclaw trace diff 20260315-054426 20260315-071830

Run A: 20260315-054426  Run B: 20260315-071830
Goal A: search for Python 3.13 new features
Goal B: search for Python 3.13 new features

Steps:  3 → 2 (-1)
Tokens: 1840 → 1200 (-640)
Cost:   $0.0073 → $0.0048
Time:   368ms → 420ms (+52ms)
```

## Debug step by step: `trace replay`

```
$ blueclaw trace replay 20260315-054426

Step 1: web_search (1ms) ✓
  input query: Python 3.13 new features
  output: Found 10 results...
[Enter] next · [q] quit >
```

## Track performance: `trace stats`

```
$ blueclaw trace stats --since 7

Trace Stats · 23 runs · last 7 days

Overview
  Total runs:     23
  Total steps:    87
  Avg steps/run:  3.8
  Avg tokens/run: 2,450
  Avg cost/run:   $0.0082
  Total cost:     $0.19

Timing
  Avg duration:    5.1s
  Median duration: 4.2s
  p95 duration:    12.3s
  Avg tool time:   2.1s (41% of wall)

Top Tools (by frequency)
  shell_command        34 calls (39%)
  web_search           28 calls (32%)
  http_request         18 calls (21%)
  file_read             7 calls (8%)

Failed Steps (3 across 2 runs · 3.4% step failure rate)
  timeout              2 (67%)
  network              1 (33%)
```

## Trace Web UI

```bash
blueclaw trace ui                    # open dashboard at localhost:8111
blueclaw trace ui --port 9000        # custom port
blueclaw trace ui --no-open          # don't auto-open browser
blueclaw trace ui --chat 12345       # serve traces for a Telegram chat
blueclaw trace ui --all-chats        # dashboard with workspace dropdown
blueclaw trace ui --live             # enable live event streaming (see below)
```

Browser-based dashboard, conversation-first. Vanilla JS SPA, light/dark theme, zero external dependencies. Tabs (hash-routed):

- **Conversations** (default, `#/conversations`) — list of every conversation across all workspaces, sortable by turns / tokens / cost / recency, with `source` and `models` badge columns. Click a row to drill in.
- **Traces** (`#/traces`) — flat per-turn list. Preserved for cross-conversation analysis (e.g. "most expensive turn this week"). One click navigates from a trace row to its parent conversation.
- **Stats** (`#/stats`) — aggregates (tool frequency, error categories, daily cost, context masking, etc.).
- **Compare** (`#/compare`) — pick two traces from the Traces view and diff them.

### Conversation timeline view

`#/conversations/<cid>` renders a vertical timeline of turns. Each turn is a collapsible card with:

- **Header bar** — turn number, timestamp, model, token count, status. When ≤10 turns the cards auto-expand; longer conversations collapse them by default.
- **Transcript** — user prompt → tool calls → assistant reply, inline. Tool use and tool result fold into one bordered "tool card" per call (name + full args + duration on the header, result content below a dashed border with show-more for long output). Plain user/assistant text is rendered in full, no truncation. Messages are sliced to just the current turn's exchange — prior turns don't bleed into the current card.
- **Deep details** (click to expand) — two sub-panels:
  - **Waterfall** combining `RunTrace.steps` (tool bars, color-coded by tool name) with model invocations parsed from `events.jsonl` (purple bars). Header reads `Waterfall — Xs wall, Ys tool, Zs model`.
  - **Raw events stream** rendering each line of `events.jsonl` color-coded by type (model = purple, tool = blue, message = green, context = orange, lesson = yellow). Per-type formatted summary (e.g. `model.before` shows `<model_id> prompt_msgs=N sys_chars=N tools=N`; `tool.after` shows `<name> id=<tu_id> status=<s> dur=<ms>`). Click any row to expand the full event JSON. Tool/message/context events nested inside a `model.before`/`model.after` span are indented for visual grouping. Capped at 1000 rows with a "download full file" link when longer.

Footer links open the raw artifacts in a new tab: `events.jsonl`, `messages.json`, `response.txt`.

### Per-turn captures in the trace list

The flat **Traces** view continues to surface captures as an inline preview chip — the first line of `response.txt`, truncated to 200 chars — with "view response" / "view messages" links that open the raw artifacts (served by `/api/turns/<cid>/<n>/{response,messages}`). Rows whose capture directory has been pruned (operator deleted `.blueclaw/conversations/<cid>/`) show a "captures pruned" badge. Pre-feature traces with no `capture_path` show an empty cell.

### Live mode

`blueclaw trace ui --live` opens a Unix domain socket at `~/.blueclaw/live.sock` (mode 0600, lock file at `~/.blueclaw/live.lock` with PID liveness check). Any blueclaw process started afterward — `blueclaw run`, `blueclaw serve`, `blueclaw telegram`, `blueclaw test` — detects the socket via the lock file and forwards every captured event to the broker in real time.

The dashboard subscribes via Server-Sent Events at `/api/conversations/<cid>/turns/<n>/events/live`. A "Live" pill in the conversation header pulses green while a turn is in flight; new tool calls, assistant text, and model events animate into the open conversation timeline as they happen. The dashboard polls `/api/conversations/<cid>` every 3 s to detect new turns, re-rendering the timeline and reopening the SSE for the new latest turn.

The handshake is gap-safe: the SSE subscribes to the broker first, then reads `events.jsonl` from disk for the backfill frame, then drains the buffered queue with dedup by `seq`. A fresh `schema.version` event resets the dedup window so events from a new turn (which restart `seq` at 0) aren't filtered as duplicates of the prior turn.

Live mode is **off by default** — the broker isn't created and no socket is published unless `--live` is on. The dashboard's "Live" pill is hidden when live mode is off; the UI is purely post-hoc in that case.

The live broker is **read-only**: the SSE channel does not accept commands from the browser. `blueclaw serve` (HTTP API gateway) does not expose the SSE route either — only the `blueclaw trace ui` dashboard does.

### Dashboard-only routes

The following routes are exposed by `blueclaw trace ui` only — `blueclaw serve` does not register them. They are read-only and not authenticated (the dashboard binds to localhost). For programmatic access against `blueclaw serve`, read the underlying files from the workspace path directly (see [docs/api.md — Per-turn capture](api.md#per-turn-capture)).

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/conversations` | Aggregates over all conversations (turn count, totals, sources, models). Honors `?workspace=<key>` (`all` unions). |
| `GET` | `/api/conversations/<cid>` | Per-conversation summary + per-turn list with `capture_path` and `has_events_jsonl` flags. |
| `GET` | `/api/conversations/<cid>/turns/<n>/events` | Streams the turn's `events.jsonl` as `application/x-ndjson`. |
| `GET` | `/api/conversations/<cid>/turns/<n>/events/live` | Server-Sent Events: backfill + live appends + end. Only available when `trace ui --live` is set. |
| `GET` | `/api/turns/<cid>/<n>/response` | Raw `response.txt` for that turn. |
| `GET` | `/api/turns/<cid>/<n>/messages` | Raw `messages.json` for that turn (full Strands message array). |
| `GET` | `/api/live/status` | Returns `{"live": bool}` reflecting whether the dashboard process has a live broker. |

All routes validate `<cid>` against path-traversal characters and `<n>` against `^[1-9]\d{0,4}$` (1..99999). Invalid IDs receive a generic 400 that does not echo the rejected value.

### Multi-workspace mode

When `--all-chats` is used, the dashboard sidebar shows a `Workspace` dropdown listing the default workspace plus every Telegram chat directory under `~/blueclaw/chats/`, plus an `All workspaces` option. Selecting `All workspaces` adds a `Source` column to the trace list and a `By source` table to the stats view.

Every `trace` subcommand accepts the same flags:

- `--chat <id>` — target a single Telegram chat's workspace.
- `--all-chats` — operate on the default workspace plus every chat (supported on `list`, `stats`, `purge`, `ui`).

Single-target commands (`show`, `explain`, `graph`, `replay`, `timeline`, `diff`) auto-scan every workspace for the given run_id; on collision they exit with a friendly disambiguation hint listing the candidate workspaces.
