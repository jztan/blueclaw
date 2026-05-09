# BlueClaw Roadmap

> Observable agent runtime → trace analytics → smart context management → agent testing → trace web UI → API gateway → stateful conversations → multi-channel runtime → production hardening.

**Current:** v2.1 complete. v2.2 next.

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

## v2.2 — Stateful Conversations

- Per-conversation memory across API requests. Wire up Strands `FileSessionManager` keyed by `conversation_id` — already validated and echoed in v2 responses but not yet used to persist history. Callers that supply the same `conversation_id` across requests get a continuous conversation; omitting it keeps the current stateless behavior.
- `blueclaw serve --install` to generate launchd/systemd service configs.

## v2.3 - Subagent support
- `Subagent` protocol for hierarchical agent structures. Subagents are lightweight agents invoked by a parent agent to handle specific tasks or domains, with their own tools and memory but no direct channel access. The parent agent can delegate to subagents via a new `invoke_subagent` tool, passing arguments and receiving structured results. This enables modular agent design and separation of concerns without the overhead of full API calls.

## v2.4 - Skill Support
- Skill.md are packaged as a directory containing SKILL.md (description and metadata), tools (Python or MCP), prompts. The blueclaw skill CLI handles creation, schema validation, and local installation.

## v3 — Multi-Channel Runtime
Channel routing layer: `ChannelAdapter` protocol and `ChannelRegistry` for dispatching messages by source, plus sender auth and SQLite-backed conversation persistence. Channel adapters for Slack, Discord, and Telegram ship as thin skill files on top of this core.

---

## Explicitly Deferred

| Feature | Reason |
|---|---|
| Task scheduling | Can be a skill, not core |
| Browser automation | Can be an MCP server, not core |
| Docker sandbox | Optional container isolation (`sandbox: docker` config, volume mount, resource caps); add when there's a real security requirement |
| Network-level domain isolation | Requires Docker proxy; deferred until Docker sandbox lands |
| OpenTelemetry export | No current need; revisit when external observability is required |
