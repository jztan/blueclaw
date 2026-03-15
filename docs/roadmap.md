# blueclaw Roadmap

> Observable agent runtime → trace analytics → agent testing → production observability → API gateway → multi-channel runtime.
> Derived from [design.md](design.md). Each milestone ships independently.
> **Current:** v1.2 complete. v1.3 next.

---

## Trace Philosophy

Agents should be observable. Every run should be inspectable. Every tool call should be traceable. Every failure should be explainable.

Most AI agents are black boxes — when something goes wrong, you don't know if it was the model reasoning, the tool input, the tool output, or a bad retry. BlueClaw treats agents like debuggable programs: structured traces capture every step, and CLI tools let you explain, replay, diff, and analyze what happened after the fact.

---

## v1 — Observable Agent Runtime (Foundation)

**Goal:** A working interactive terminal agent with built-in structured execution tracing. Small enough to read in one sitting.

| Deliverable | File(s) | Notes |
|---|---|---|
| Interactive terminal session with Rich welcome banner + pixel art mascot | `cli.py` | Typer entrypoint, prompt_toolkit input, Rich output ✅ |
| Strands Agent as the core loop | `session.py` | Loads CONTEXT.md + history.jsonl, builds Agent, runs chat loop ✅ |
| Model-agnostic support (Claude, Ollama, Gemini) | `session.py`, `models.py` | Factory for AnthropicModel / OllamaModel / LiteLLMModel via config ✅ |
| Persistent memory: `CONTEXT.md` + `history.jsonl` | `session.py` | Read on start, background update after each turn; JSONL append-only audit trail ✅ |
| Custom tools via `@tool` (web) + MCP (pdf) | `tools/web.py`, `tools/__init__.py` | MCP-first where possible; PDF via `pdf-mcp` server ✅ |
| Shell command execution (`shell_command`) | `tools/shell.py`, `approval.py`, `workspace.py` | Sandboxed `subprocess.run()` in workspace; deny-list + interactive approval; enables `gh` CLI and general shell access ✅ |
| MCP server support | `session.py` | Via Strands `MCPClient` ✅ |
| Workspace sandbox (app-level) | `workspace.py` | Path validation + destructive command deny-list ✅ |
| Observability via Strands hooks | `observer.py` | `BeforeToolCallEvent` / `AfterToolCallEvent` → Rich trace + history.jsonl ✅ |
| Structured execution tracing | `observer.py`, `models.py`, `workspace.py` | `TraceStep` / `RunTrace` models, per-run JSON traces in `.blueclaw/traces/`, input/output summaries, per-step timing ✅ |
| `blueclaw trace list` and `trace show` | `cli.py` | CLI trace viewer with Rich table output ✅ |
| Tool output truncation (12k char, head+tail) | `observer.py` | Prevents context blowout ✅ |
| Conversational approval for risky actions | `approval.py` | Domain allowlist, surfaced in chat ✅ |
| Scripted mode: `blueclaw run "..."` | `cli.py` | Minimal header, run, exit ✅ |
| `blueclaw init` and `blueclaw history` commands | `cli.py` | Workspace setup + run history viewer ✅ |
| Pydantic data models | `models.py` | `RunRecord`, `SessionConfig`, `TraceStep`, `RunTrace` ✅ |
| Progressive skill loading | `session.py` | Skill index in system prompt, full content loaded on demand via file_read ✅ |
| Code quality tooling | `.flake8`, `pyproject.toml` | flake8 + black in dev deps, all source formatted ✅ |
| Test suite | `tests/` | 238 tests covering models, workspace, observer, CLI, session, tools, shell, approval, integration ✅ |

**Core files:** 6 modules (`cli.py`, `session.py`, `tools/`, `workspace.py`, `observer.py`, `models.py`, `approval.py`)
**Actual lines:** 1,554
**Dependencies:** strands-agents, strands-agents-tools, typer, rich, prompt-toolkit, pydantic, pyyaml + dev: pytest, pytest-mock, flake8, black

---

## v1.1 — Trace Analysis Tools

**Goal:** Make BlueClaw runs debuggable after the fact. CLI tools for explanation, comparison, and visualization.

| Deliverable | File(s) | Status | Notes |
|---|---|---|---|
| `blueclaw trace explain <run_id>` | `cli.py`, `session.py` | ✅ | Feed recorded trace to LLM for post-hoc explanation. Fresh tool-free Agent with default streaming callback. |
| `blueclaw trace graph <run_id>` | `cli.py` | ✅ | Rich Tree rendering of tool call sequences with status icons, timing, and input summaries |
| `blueclaw trace diff <id1> <id2>` | `cli.py` | ✅ | Side-by-side comparison of two runs (steps, timing, cost, tokens with deltas) |
| `blueclaw trace replay <run_id>` | `cli.py` | ✅ | Interactive step-through viewer — Enter to advance, q to quit |

**Actual lines:** 1,728
**Test coverage:** 265 tests (+33 for v1.1 trace tooling)

---

## v1.2 — Trace Analytics & Stats

**Goal:** Aggregate trace data into actionable metrics. Answer "how is my agent performing?" without reading individual traces.

| Deliverable | File(s) | Status | Notes |
|---|---|---|---|
| `blueclaw trace timeline <run_id>` | `cli.py` | ✅ | Waterfall timeline with per-step start offset, duration, cumulative timing, proportional bar chart, and overhead breakdown |
| `blueclaw trace stats` | `cli.py`, `workspace.py` | ✅ | Aggregate stats: run count, avg latency, timing percentiles (median/p95), most-used tools, avg tokens/cost per run. `--since N` and `--model` filters |
| Failure classification | `cli.py`, `models.py` | ✅ | `classify_error()` groups step-level failures by type: timeout, rate_limit, auth, not_found, schema, network, sandbox. Surfaced in `trace stats` output |
| Token/cost breakdown per step | `models.py` | ✅ | Optional `tokens` and `cost` fields on `TraceStep` (forward-compatible — populated when per-tool metrics become available from Strands SDK) |
| Date-range filtering | `workspace.py` | ✅ | `since` parameter on `Workspace.list_traces()` for date-range trace queries |
| Current date in system prompt | `session.py` | ✅ | `build_system_prompt()` includes UTC date so the agent knows "today" without relying on training cutoff |
| Esc Esc user interrupt | `observer.py` | ✅ | Double-Esc stops agent at next tool boundary via `cancel_tool` + `stop_event_loop`. Non-blocking cbreak stdin polling with escape-sequence disambiguation |
| Honest `web_search` placeholder | `tools/web.py` | ✅ | Returns "not available" instead of fake results — prevents runaway `http_request` retry loops |

**Actual lines:** 2,035
**Test coverage:** 316 tests (+51 for v1.2 analytics + interrupt)

---

## v1.3 — Agent Regression Testing

**Goal:** Define expected agent behavior in YAML and validate it automatically. CI for agents.

| Deliverable | File(s) | Status | Notes |
|---|---|---|---|
| `blueclaw test <spec.yaml>` | `cli.py`, `testing.py` (new) | ⬚ | Run goals from YAML spec, assert expected tools were called, check pass/fail |
| Test spec format | — | ⬚ | YAML with `goal`, `expected_tools`, optional `expected_output_contains`, `max_steps`, `max_cost` |
| JUnit/TAP output | `testing.py` | ⬚ | Machine-readable test results for CI integration |
| `blueclaw test --dry-run` | `cli.py` | ⬚ | Validate spec without running agents |
| `blueclaw trace replay --stub-tools` | `cli.py` | ⬚ | Re-run model reasoning with recorded tool outputs — debug prompt/reasoning changes without network or API calls |

**Example spec:**
```yaml
tests:
  - goal: summarize webpage
    expected_tools: [fetch_url, summarize]
    max_cost: 0.05

  - goal: create a file called hello.txt
    expected_tools: [shell_command]
    expected_output_contains: hello.txt
```

**Estimated lines:** ~1,950
**New file:** `testing.py` (~120 lines)

---

## v2 — Production Observability

**Goal:** Move trace storage beyond local JSON files. Production-grade persistence and optional visualization. This strengthens the core identity before adding API plumbing.

### v2.0 — Trace Storage Backend

| Deliverable | File(s) | Status | Notes |
|---|---|---|---|
| SQLite trace store | `trace_store.py` (new) | ⬚ | Replace/augment JSON files with SQLite for querying, aggregation, retention |
| `blueclaw trace query` | `cli.py` | ⬚ | SQL-like filtering: `--tool web_search --status error --since 7d` |
| Trace retention policy | `trace_store.py` | ⬚ | Auto-prune traces older than N days, configurable |
| OpenTelemetry export (optional) | `trace_store.py` | ⬚ | Export traces as OTel spans for Jaeger/Datadog/Grafana integration |

### v2.1 — Trace UI (Optional)

| Deliverable | File(s) | Status | Notes |
|---|---|---|---|
| `blueclaw trace serve` | `trace_ui.py` (new) | ⬚ | Local web UI for trace visualization — call graph, timeline, cost breakdown |
| Trace timeline visualization | `trace_ui.py` | ⬚ | Waterfall chart of tool calls with timing bars |

---

## v3 — Agent API Gateway

**Goal:** A single API endpoint so other systems can talk to BlueClaw. Lightweight — not a full messaging platform.

### v3.0 — Stateless Webhook

| Deliverable | File(s) | Status | Notes |
|---|---|---|---|
| `POST /message` endpoint + `GET /health` | `server.py` (new, ~100 lines) | ⬚ | Reuses same `session.py` Agent as terminal mode |
| `blueclaw serve` command | `cli.py` (+10 lines) | ⬚ | Starts uvicorn on localhost:8420 |
| Quiet observer mode for webhook | `observer.py` (+10 lines) | ⬚ | Suppress Rich console output in API mode |
| `ServerConfig`, `MessageRequest`, `MessageResponse` models | `models.py` (+15 lines) | ⬚ | Forward-compatible API contract |

**Core files:** 7 (+server.py)
**New dependencies:** fastapi, uvicorn (optional)

### v3.1 — Stateful Conversations

| Deliverable | File(s) | Status | Notes |
|---|---|---|---|
| Per-conversation persistence via Strands `FileSessionManager` | `server.py`, `session.py` | ⬚ | `build_webhook_agent(conversation_id, session_manager)` helper |
| `conversation_id` tracking | `server.py` | ⬚ | Missing ID → random one-shot ID (preserves stateless behavior) |

---

## v4 — Multi-Channel Runtime

**Goal:** Production multi-channel agent. Lower priority than observability — many frameworks already do this.

| Deliverable | File(s) | Status | Notes |
|---|---|---|---|
| Channel adapters as skills | skills/ | ⬚ | Slack, Discord, Telegram — thin translators to `POST /message` |
| `ChannelAdapter` protocol + `ChannelRegistry` | `channels.py` (new) | ⬚ | Protocol-based, no base class inheritance |
| Conversation routing + sender auth | `server.py` | ⬚ | SQLite-backed conversation persistence |
| Docker sandbox (optional) | `workspace.py` | ⬚ | `sandbox: docker` config, volume mount, resource caps |
| `blueclaw serve --install` | `cli.py` | ⬚ | Generate launchd/systemd service config |

---

## Persistence Layers

| Store | Purpose | Reader | Lifetime | Status |
|---|---|---|---|---|
| `CONTEXT.md` | Agent's semantic memory | Agent + human | Per-workspace, human-editable | ✅ |
| `history.jsonl` | Run audit log | `blueclaw history` | Append-only | ✅ |
| `traces/*.json` | Structured execution traces | `blueclaw trace` | Per-run | ✅ |
| `FileSessionManager` | Strands conversation state | Agent (context restore) | Per-conversation | ⬚ |
| SQLite (`blueclaw.db`) | Trace queries, conversations | `trace query`, API | Per-workspace | ⬚ |

---

## Migration Path

- **v1 → v1.1:** Extend trace CLI. Existing terminal mode unchanged.
- **v1.1 → v1.2:** Add analytics commands. No schema changes — reads existing trace JSON.
- **v1.2 → v1.3:** Add test runner. Existing traces/agent unchanged.
- **v1.3 → v2:** SQLite trace store alongside existing JSON (migration optional). OTel export opt-in.
- **v2 → v3:** Add `server.py`. Existing terminal mode unchanged.
- **v3 → v4:** Channel adapters as skills, not core. Existing API unchanged.

---

## Explicitly Deferred

| Feature | Reason |
|---|---|
| Task scheduling | Can be a skill, not core |
| Multi-agent collaboration | Strands supports it — add when there's a real use case |
| Browser automation | Can be an MCP server, not core |
| Network-level domain isolation | Requires Docker proxy; deferred to v4 |

---

## Complexity Budget

| Metric | v1 | v1.1 | v1.2 | v1.3 | v2 | v3 | v4 |
|---|---|---|---|---|---|---|---|
| Core files | 6 | 6 | 6 | 7 | 8 | 9 | 11 |
| Dependencies | 7 | 7 | 7 | 7 | 7 | 7 + 2 optional | 7 + 2 |
| Lines (actual/est.) | 1,554 | 1,728 | 2,035 | ~2,150 | ~2,400 | ~2,550 | ~2,800 |
| One-sitting readable? | Yes | Yes | Yes | Yes | Stretch | Stretch | No |

---

*Derived from [design.md](design.md) — last updated 2026-03-15*
