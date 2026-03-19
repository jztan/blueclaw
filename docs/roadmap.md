# BlueClaw Roadmap

> Observable agent runtime → trace analytics → smart context management → agent testing → production observability → API gateway → multi-channel runtime.

**Current:** v1.3 complete. v1.4 next.

---

## v1 — Observable Agent Runtime ✅

Working interactive terminal agent with built-in structured execution tracing. Model-agnostic (Claude, Ollama, OpenAI, Gemini). Persistent memory, workspace sandbox, shell execution, MCP support.

## v1.1 — Trace Analysis Tools ✅

CLI tools for post-hoc debugging: `trace explain`, `trace graph`, `trace diff`, `trace replay`.

## v1.2 — Trace Analytics & Stats ✅

Aggregate metrics across runs: `trace timeline`, `trace stats` with `--since` and `--model` filters. Failure classification, Esc Esc interrupt, trace-powered behavioral lessons, DuckDuckGo web search.

## v1.3 — Smart Context Management ✅

Replace LLM-based conversation summarization with observation masking — a research-backed strategy that reduces per-run costs with no quality loss. Based on [Lindenbauer et al. 2025](https://arxiv.org/abs/2508.21433). Configurable strategies (`mask`, `summarize`, `hybrid`), context metrics in traces, cost savings visible in `trace stats`. Includes `bench_context.py` benchmark runner with workload-categorized prompt files.

## v1.4 — Agent Regression Testing

Define expected agent behavior in YAML and validate automatically. CI for agents.

- `blueclaw test <spec.yaml>` — run goals, assert expected tools and outputs
- JUnit/TAP output for CI integration
- `blueclaw test --dry-run` — validate spec without running agents
- `blueclaw trace replay --stub-tools` — replay model reasoning with recorded tool outputs

## v2 — Production Observability

Move trace storage beyond local JSON files. SQLite backend, `trace query` with filtering, retention policies, optional OpenTelemetry export. Optional local web UI for trace visualization.

## v3 — Agent API Gateway

`POST /message` endpoint via `blueclaw serve`. Stateless webhook first, then per-conversation persistence via Strands `FileSessionManager`.

## v4 — Multi-Channel Runtime

Channel adapters (Slack, Discord, Telegram) as skills, conversation routing, optional Docker sandbox.

---

## Explicitly Deferred

| Feature | Reason |
|---|---|
| Task scheduling | Can be a skill, not core |
| Multi-agent collaboration | Add when there's a real use case |
| Browser automation | Can be an MCP server, not core |
| Network-level domain isolation | Requires Docker; deferred to v4 |
