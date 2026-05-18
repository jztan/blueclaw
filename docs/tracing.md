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
```

Browser-based dashboard with 4 views: trace list with search/filter, trace detail with interactive waterfall timeline, side-by-side trace comparison, and aggregate stats with charts. Light/dark theme, auto-refresh, zero external dependencies.

### Per-turn captures in the trace list

Rows that have an associated per-turn capture (terminal/HTTP/Telegram) render a "Captures" cell with an inline preview chip — the first line of `response.txt`, truncated to 200 chars. Click the chip to expand and reveal "view response" / "view messages" links that open the raw artifacts in a new tab (served by `/api/turns/<cid>/<n>/{response,messages}`). Rows whose capture directory has been pruned (operator deleted `.blueclaw/conversations/<cid>/`) show a "captures pruned" badge in place of the chip. Pre-feature traces with no `capture_path` show an empty cell.

### Multi-workspace mode

When `--all-chats` is used, the dashboard sidebar shows a `Workspace` dropdown listing the default workspace plus every Telegram chat directory under `~/blueclaw/chats/`, plus an `All workspaces` option. Selecting `All workspaces` adds a `Source` column to the trace list and a `By source` table to the stats view.

Every `trace` subcommand accepts the same flags:

- `--chat <id>` — target a single Telegram chat's workspace.
- `--all-chats` — operate on the default workspace plus every chat (supported on `list`, `stats`, `purge`, `ui`).

Single-target commands (`show`, `explain`, `graph`, `replay`, `timeline`, `diff`) auto-scan every workspace for the given run_id; on collision they exit with a friendly disambiguation hint listing the candidate workspaces.
