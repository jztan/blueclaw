# BlueClaw Roadmap

> Observable agent runtime → trace analytics → smart context management → agent testing → trace web UI → API gateway → stateful conversations → sandbox hardening → conversation-first observability → subagents → multi-channel runtime.

**Current:** v2.5 released on `master`. v3 staged on `develop` (conversation-first trace UI + live streaming) — awaiting tag. Future work pooled below; versions assigned on shipment.

---

## v1 — Observable Agent Runtime ✅

Working interactive terminal agent with built-in structured execution tracing. Model-agnostic (Claude, Ollama, OpenAI, Gemini). Persistent memory, workspace sandbox, shell execution, MCP support.

## v1.1 — Trace Analysis Tools ✅

CLI tools for post-hoc debugging: `trace explain`, `trace graph`, `trace diff`, `trace replay`.

## v1.2 — Trace Analytics & Stats ✅

Aggregate metrics across runs: `trace timeline`, `trace stats` with `--since` and `--model` filters. Failure classification, Esc Esc interrupt, trace-powered behavioral lessons, DuckDuckGo web search.

## v1.3 — Smart Context Management ✅

Replace LLM-based conversation summarization with observation masking — a research-backed strategy that reduces per-run costs with no quality loss. Based on [Lindenbauer et al. 2025](https://arxiv.org/abs/2508.21433). Configurable strategies (`mask`, `summarize`, `hybrid`), context metrics in traces, cost savings visible in `trace stats`. Includes `bench_context.py` benchmark runner with workload-categorized prompt files.

## v1.4 — Agent Regression Testing ✅

Define expected agent behavior in YAML and validate automatically. CI for agents.

- `blueclaw test <spec.yaml>` — run goals, check 11 deterministic assertions (tools, output, files, cost, duration, regex, tool order)
- TAP/JUnit output for CI integration
- Multi-run with Wilson CI scoring for statistically valid pass/fail/inconclusive verdicts
- `blueclaw test --dry-run` — validate spec without running agents
- `blueclaw test --keep-workspace` — preserve run workspaces with per-run `result.json` diagnostics
- `allowlist_domains` on test specs — declare domains for `http_request` without editing `blueclaw.yaml`
- `blueclaw trace replay --stub-tools` — replay model reasoning with recorded tool outputs

## v1.5 — Trace Web UI ✅

Local browser-based dashboard for trace visualization. `blueclaw trace ui` serves a self-contained single-page app on localhost — no npm, no node, no external dependencies.

- Trace list with search/filter (goal, model, status, date range)
- Trace detail with interactive waterfall timeline and expandable steps
- Side-by-side trace comparison with delta indicators
- Stats dashboard with charts (tool frequency, cost over time, timing distribution, error breakdown)

## v2 — Agent API Gateway ✅

Expose the agent over HTTP via `blueclaw serve`. `POST /message` returns a reply, run ID, token count, and cost. Bearer token auth (`BLUECLAW_API_KEY`), 1 MB body cap, 300 s timeout, CORS for localhost. Per-conversation context persistence via Strands `FileSessionManager`. Every API request writes a trace visible in `blueclaw trace ui`.

## v2.1 — API Hardening ✅

Concurrency and streaming for the HTTP API. A shared `asyncio.Semaphore` (default 4, configurable via `server.max_concurrent_runs` or `--max-concurrent`) caps simultaneous agent runs across `/message` and `/message/stream` to prevent resource exhaustion under load. The new `POST /message/stream` endpoint emits Server-Sent Events with token-by-token `delta` chunks followed by a `done` payload carrying `run_id`, tokens, and cost — callers see output as it is generated rather than waiting for the full reply.

## v2.2 - Stateful conversations ✅
Per-conversation memory now persists via Strands `FileSessionManager` keyed by `conversation_id`. Callers that supply the same id across requests get a continuous conversation; omitting it keeps stateless behavior. Concurrent requests for the same id are serialized by a per-id lock; different ids run in parallel.

## v2.3 — File Uploads & Native Vision ✅

Multi-modal input for the API and CLI. `POST /upload` (multipart, 25 MB cap) accepts PDFs, images, and common text/data formats and returns a `file_id` scoped to a conversation; `POST /message` accepts a `file_ids` list (max 10) that the server resolves to absolute paths. Image attachments (PNG/JPEG/GIF/WEBP) reach vision-capable models as Strands `image` content blocks rather than path notes, while PDFs and text continue through the path-prefix flow so existing shell and pdf-mcp tools handle them. The CLI mirrors the same UX: `@<path>` in any prompt — or a bare/quoted absolute path pasted via shift+drag — auto-attaches. The bundled playground gains a paperclip button, drag-and-drop, removable chips, and a light theme.

## v2.4 — Skill Support ✅

Skills are directories containing a `SKILL.md` (YAML frontmatter + markdown body) following the [AgentSkills.io](https://agentskills.io) standard, loaded at runtime via the Strands `AgentSkills` plugin (1.30+). The `blueclaw skill` CLI installs from local paths, git URLs (with optional `#subdir`), or direct HTTPS to raw `SKILL.md`; `uninstall`, `list`, and `show` round out management. User-global skills live under `~/blueclaw/skills/`; per-project skills under `<project>/.blueclaw/skills/` shadow the global scope on name collision. Skills in v2.4 are pure prompt + metadata — Python tools and MCP refs are deferred to a later release.

## v2.5 — Docker Sandbox ✅

Optional whole-agent container isolation. A new `sandbox: docker` mode in `blueclaw.yaml` runs the entire `blueclaw` process inside a short-lived container with the workspace bind-mounted read-write and the rest of the host filesystem invisible — every tool call, shell or otherwise, inherits the same boundary. Configurable resource caps (CPU, memory, pid limit, wall-clock timeout) and a network mode toggle (`bridge` | `none`; `proxy` reserved for v3) replace the app-level deny-list as the primary security boundary. Falls back transparently to the in-process sandbox when Docker is unavailable, so dev loops stay fast. Sets the foundation for network-level domain isolation (egress proxy enforcing the allowlist instead of trust-the-tool).

## v3 — Conversation-first Observability + Live Streaming

Trace UI v2: the dashboard is conversation-first instead of trace-first, every turn captures a structured event stream, and a Unix-socket broker lets a running agent stream events into the dashboard live. Staged on `develop` (commit range `master..develop`); not yet tagged.

- **Capture layer.** Every turn now writes a per-turn `events.jsonl` alongside `response.txt` / `messages.json`. Captures tool calls, model invocations, message additions, observation masking, and lesson injection with monotonic `seq` and a `schema.version` header. `runner.bus_for_turn(observer, capture_path)` is the single chokepoint that wires the bus into every adapter (terminal, HTTP, Telegram, eval) and fans out to bus-aware components reachable from the observer.
- **Conversation-first dashboard.** New `#/conversations` and `#/conversations/<cid>` views. Per-turn transcript with user → tool → assistant inline; tool use and tool result fold into one bordered tool card with full args, full result, and show-more for long output. Deep details panel combines `RunTrace.steps` (tool bars) with model invocations from `events.jsonl` (purple bars) in a single waterfall, plus a virtualized color-coded raw events stream with per-type formatted summaries.
- **Backend conversation API.** `GET /api/conversations`, `/api/conversations/<cid>`, `/api/conversations/<cid>/turns/<n>/events` expose per-cid aggregates and per-turn streams — computed at query time from existing trace files, no new persistence files.
- **Live event streaming.** `blueclaw trace ui --live` opens a Unix-socket broker at `~/.blueclaw/live.sock`. Any blueclaw process started afterward detects the socket and forwards every captured event. The dashboard subscribes via SSE at `/api/conversations/<cid>/turns/<n>/events/live` with a gap-safe backfill + dedup-by-seq handshake, plus a 3-second poll for new turns so live updates survive across producer lifetimes. Off by default; opt in with `--live`.

---

## Planned (unversioned)

Candidates for the next release cycle. Versions are assigned when scope and ship date solidify.

### Subagent support

`Subagent` protocol for hierarchical agent structures. Subagents are lightweight agents invoked by a parent agent to handle specific tasks or domains, with their own tools and memory but no direct channel access. The parent agent can delegate to subagents via a new `invoke_subagent` tool, passing arguments and receiving structured results. This enables modular agent design and separation of concerns without the overhead of full API calls. With v2.5's container sandbox in place, subagent-spawned shell work runs inside the same isolation boundary.

### Multi-Channel Runtime

Channel routing layer: `ChannelAdapter` protocol and `ChannelRegistry` for dispatching messages by source, plus sender auth and SQLite-backed conversation persistence. Channel adapters for Slack and Discord ship as thin skill files on top of this core. The Telegram bridge has landed early (see `docs/bridges/telegram.md`) — allowlist-enforced, per-chat workspaces, long-polling default — and will be retrofitted onto the `ChannelAdapter` protocol when it lands.

---

## Explicitly Deferred

| Feature | Reason |
|---|---|
| Task scheduling | Can be a skill, not core |
| Browser automation | Can be an MCP server, not core |
| OpenTelemetry export | No current need; revisit when external observability is required |
