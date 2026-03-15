# Changelog

All notable changes to blueclaw will be documented in this file.

## [1.2.0] - 2026-03-15

### Added

- `blueclaw trace timeline <run_id>` — waterfall timeline showing when each tool call started, duration, cumulative timing, and proportional bar chart with overhead breakdown
- `blueclaw trace stats` — aggregate metrics across all traces: run count, step counts, avg tokens/cost, timing percentiles (median, p95), top tools by frequency, and step-level failure classification
- `--since N` and `--model MODEL` filters for `trace stats`
- `classify_error()` — heuristic error classifier (timeout, rate_limit, auth, not_found, schema, network, sandbox) for step-level failure aggregation
- `since` parameter on `Workspace.list_traces()` for date-range filtering
- Optional `tokens` and `cost` fields on `TraceStep` (forward-compatible for per-step attribution)
- Current date injected into system prompt so the agent knows "today" without relying on training cutoff
- Esc Esc interrupt — press Escape twice during agent execution to stop the current turn at the next tool boundary. Non-blocking cbreak stdin polling in `before_tool` hook with escape-sequence disambiguation (50ms peek)
- Trace Lessons — before each turn, scans past traces for similar goals with failures/cost spikes, injects up to 3 short behavioral hints into the prompt. Reduced repeat oil-price query from 6 tool calls/$0.11 to 1 call/$0.03. Jaccard keyword similarity with minimal stemming, capped at 50 recent traces
- 72 new tests for v1.2 features (337 total)

### Fixed

- `web_search` placeholder returned fake `"Search results for: {query}"` string, causing the model to spiral into 20+ `http_request` retries. Now returns an honest "not available" message

## [1.1.0] - 2026-03-15

### Added

- `blueclaw trace explain <run_id>` — LLM-powered post-hoc explanation of recorded traces using a fresh tool-free Agent
- `blueclaw trace graph <run_id>` — Rich Tree rendering of tool call sequences with status icons, timing, and input summaries
- `blueclaw trace diff <id1> <id2>` — side-by-side comparison of two runs (steps, tokens, cost, duration with deltas)
- `blueclaw trace replay <run_id>` — interactive step-through viewer (Enter to advance, q to quit)
- `format_trace_for_explanation()` helper in session.py
- Shared `sample_trace` and `error_trace` test fixtures in conftest.py
- 33 new tests for trace tooling (265 total)

## [1.0.0] - 2026-03-15

### Added

- Interactive terminal session with Rich welcome banner and pixel art mascot
- Scripted mode: `blueclaw run "..."` for one-shot execution
- `blueclaw init` — workspace initialization
- `blueclaw history` — view past run history
- `blueclaw --version` — version display
- Model-agnostic support: Anthropic, Ollama, OpenAI, LiteLLM via `--model` flag or `blueclaw.yaml`
- API key validation for Anthropic and OpenAI providers
- `.env` file support via python-dotenv
- Workspace sandbox with path validation and destructive command deny-list (sudo, curl|bash, wget|sh)
- Shell command execution via `shell_command` tool: sandboxed `subprocess.run()` with 30s timeout, workspace cwd
- Protected files (CONTEXT.md, history.jsonl, last_turn.md) blocked from shell access at sandbox level
- Structured execution tracing: per-run JSON traces in `.blueclaw/traces/`
- `TraceStep` / `RunTrace` models with input/output summaries, timing, and error capture
- `blueclaw trace list` — list recent execution traces
- `blueclaw trace show <run_id>` — display detailed step table for a run
- Tool output truncation (12k char limit, head+tail preservation)
- Test coverage for trace models, workspace trace I/O, observer step accumulation, CLI trace commands, shell tool, and approval hooks (238 tests total)
- flake8 and black dev dependencies with `.flake8` config
- Domain allowlist with conversational approval hooks
- Persistent context: `CONTEXT.md` updated via background LLM summarization after each turn (instant exit)
- Append-only run history: `.blueclaw/history.jsonl`
- Crash recovery checkpointing (`.blueclaw/last_turn.md`)
- End-of-run summary: steps, tokens, cost, elapsed time
- Progressive skill loading (index in system prompt, not full content)
- MCP server support: pdf-mcp (bundled), custom stdio/SSE servers via config
- MCP client cleanup on session exit
- Custom web tools via `@tool` factory pattern with domain allowlist injection
- SummarizingConversationManager for within-session context compression
- Quiet observer mode (for future webhook/API use)
