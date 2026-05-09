# Changelog

All notable changes to blueclaw will be documented in this file.

## [Unreleased]

### Added
- Stateful conversations: when `POST /message` (or `/message/stream`) supplies a `conversation_id`, history is persisted via Strands `FileSessionManager` under `<workspace>/.blueclaw/sessions/<id>/`. Subsequent requests with the same id replay prior turns. Omitting `conversation_id` keeps stateless behavior.
- `conversation_id` field on `RunTrace` and `RunRecord` (also exposed in `/api/traces` summary) so traces and history rows can be grouped by conversation.
- `GET /playground` — single-page chat UI bundled with `blueclaw serve` for manually exercising stateful + streaming conversations. Defaults its server URL to the current origin; bearer token entered in the sidebar. Unauthenticated like `/health`.

### Changed
- `build_trace_and_record(...)` accepts an optional `conversation_id` kwarg.

### Notes
- Concurrent requests for the same `conversation_id` are serialized by an in-process per-id lock, acquired *before* the global concurrency semaphore. Different conversation ids run in parallel (subject to the existing `max_concurrent_runs` cap).
- Session directories are purged on server start by `purge_old_sessions(trace_retention_days)` (no new config knob).

## [2.1.0] - 2026-05-06
### Added

- `POST /message/stream` — Server-Sent Events endpoint emitting token-by-token `event: delta` chunks followed by an `event: done` payload (reply, run_id, tokens, cost). Same auth, body cap, and trace recording as `/message`. Errors after the stream opens are signaled via `event: error`
- `SessionConfig.max_concurrent_runs` (default `4`) — `asyncio.Semaphore` shared across `/message` and `/message/stream` caps simultaneous agent runs to prevent resource exhaustion
- `server.max_concurrent_runs` config key in `blueclaw.yaml`
- `--max-concurrent` flag on `blueclaw serve` to override the cap

### Fixed

- `TestTracePurge` CI failures — `test_purge_deletes_old_traces` and `test_purge_keeps_recent_traces` used hardcoded date `20260315` which aged past the 30-day retention window; replaced with dates computed dynamically relative to `datetime.now()`

## [2.0.0] - 2026-03-22
### Added

- `blueclaw serve` — local HTTP API server (`POST /message`, `GET /health`) exposing the agent over HTTP with Bearer token auth (`BLUECLAW_API_KEY`), 1 MB body cap, 300 s timeout, and CORS for localhost/127.0.0.1
- `MessageRequest` and `MessageResponse` Pydantic models for the API request/response contract
- `RunTrace.source` field (`"terminal"` | `"api"`) — API traces are tagged and visible in `blueclaw trace ui`
- `build_trace_and_record()` extracted from `print_run_summary()` — pure function, shared by terminal and API paths
- `callback_handler` and `session_manager` parameters on `create_agent()` — `None` suppresses streaming for API use
- `Workspace.purge_old_sessions()` — cleans up old session directories on server startup
- `--host`, `--port`, `--model`, `--cors-origin` flags on `blueclaw serve`

### Fixed

- Trace UI rejected API-format run IDs (`YYYYMMDD-HHMMSS-<4hex>`) — regex updated to `^\d{8}-\d{6}(-[0-9a-f]{4})?$`

## [1.5.0] - 2026-03-21
### Added

- `blueclaw trace ui` — local browser dashboard for trace visualization with 4 views (list, detail, compare, stats), light/dark theme, auto-refresh
- `compute_stats()` extracted from `trace stats` CLI into reusable function shared by CLI and web API
- REST API: `/api/traces`, `/api/traces/{run_id}`, `/api/stats` for trace data access
- Path traversal guard on trace API run_id parameter

## [1.4.1] - 2026-03-20
### Fixed

- CI test failure: `patch("blueclaw.cli.Path")` broke Typer type resolution after adding `spec_path: Path` parameter — replaced with `patch("blueclaw.session.load_config")`
- CI test failure: ANSI escape codes in Rich help output hid `--stub-tools` flag from string match — strip escape codes before assertion

## [1.4.0] - 2026-03-20
### Added

- Per-run `result.json` written to each run workspace for `--keep-workspace` inspection
- Inconclusive test verdicts now show failure diagnostics in TAP and JUnit output
- `allowlist_domains` field on test specs — tests can declare domains needed for `http_request` without editing `blueclaw.yaml`
- `blueclaw test` — YAML-driven agent regression testing with TAP/JUnit output, Wilson CI scoring for multi-run statistical verdicts, and CI-friendly exit codes
- `--keep-workspace` flag for `blueclaw test` — preserves temp workspace for post-test inspection instead of cleaning up
- 11 test assertions: `expected_tools`, `expected_output_contains`, `max_steps`, `max_cost`, `forbidden_tools`, `expected_files`, `expected_file_contains`, `forbidden_output_contains`, `output_regex`, `tool_order`, `max_duration_s` — all deterministic, no LLM-as-judge
- Spec validation: contradictory tools, invalid regex, negative duration warnings
- Path traversal guard on `expected_files` and `expected_file_contains`

## [1.3.0] - 2026-03-19
### Added

- Smart context management: `ObservationMaskingManager` replaces `SummarizingConversationManager` as the default conversation manager. Based on [Lindenbauer et al. 2025 "The Complexity Trap"](https://arxiv.org/abs/2508.21433) — observation masking halves per-run costs with no quality loss
- Three configurable strategies via `blueclaw.yaml`: `mask` (default, replaces old tool outputs with placeholders), `summarize` (legacy LLM summarization), `hybrid` (mask first, summarize only after N turns)
- `context` section in `blueclaw.yaml`: `strategy`, `mask_after` (default 10), `summarize_after` (hybrid only, default 43)
- Context metrics in traces: `context_masked_chars` and `context_strategy` fields on `RunTrace`
- `trace show` displays context strategy and masked char count when present
- `trace stats` shows Context Management section: runs with masking, avg/total chars masked, strategy breakdown
- `BeforeModelCallEvent` hook for proactive masking within multi-tool invocations (same pattern as Strands' `SlidingWindowConversationManager`)
- `reduce_context` fallback chain: aggressive mask (M=0) then delegate to `SummarizingConversationManager`
- 35 new tests (400 total)
- `scripts/bench_context.py` — multi-turn benchmark runner for comparing context strategies. Delta token tracking, isolated workspaces per strategy, error recovery, response capture. Supports `--strategy`, `--model`, `--mask-after`, `--output` flags
- Benchmark prompt files for 3 workload categories: `search-small` (small outputs), `retrieval-large` (full page fetches), `mixed-workflow` (search + fetch)

### Changed

- Default conversation manager switched from `SummarizingConversationManager` to `ObservationMaskingManager` — existing behavior available via `context.strategy: summarize`

## [1.2.5] - 2026-03-16
### Added

- Trace retention: auto-purge old traces on session start (default 30 days, configurable via `workspace.trace_retention_days` in `blueclaw.yaml`, set `0` to keep forever)
- `blueclaw trace purge` CLI command with `--older-than` and `--dry-run` flags
- 10 new tests for trace purge (workspace, CLI, config loading)
- `workspace` section in generated `blueclaw.yaml` from `blueclaw init` (path + trace_retention_days)
- `scripts/release.py` — gitflow release automation (version bump, changelog, tag, GitHub release, PyPI wait, merge back)

### Changed

- Welcome banner: removed tips section, deduplicated version display, consolidated run count and status flags into mascot lines
- Terminal mascot alignment: centered eyes/claws over body, reduced left padding to hug panel edge

### Fixed

- `blueclaw.yaml` was tracked in git — removed from tracking and added to `.gitignore`

## [1.2.4] - 2026-03-15

### Fixed

- Unused imports (F401) in 10 test files and conftest
- Unused variables (F841) in test_cli, test_integration, test_workspace
- Ambiguous variable name `l` (E741) in test_lessons
- black formatting violations in `lessons.py` and `test_lessons.py`

## [1.2.3] - 2026-03-15

### Fixed

- CI test failures for `TestBuildModel` (anthropic, litellm, openai) — `patch("strands.models.XModel")` triggered lazy `__getattr__` which imported real SDK packages not installed in CI. Replaced with `patch.dict` on module `__dict__` to bypass lazy imports
- 7 flake8 E501 line-too-long violations in `session.py` system prompt strings
- Logo not displaying on PyPI — relative image path replaced with absolute raw GitHub URL

## [1.2.2] - 2026-03-15

### Added

- GitHub Actions CI: lint (flake8 + black) and test across Python 3.11–3.14 on push/PR to `develop`
- Issue management workflows: auto-lock closed issues after 7 days, stale issue warnings at 14 days with auto-close at 30 days, autoclose label removal on human comment
- PyPI publish workflow: tag pushes (`v*.*.*`) run full test matrix then auto-publish via twine
- README badges: PyPI version, license, Python versions, GitHub issues, CI status, downloads
- PyPI package metadata: description, license, authors, classifiers, project URLs
- Published to PyPI as `blueclaw`

### Fixed

- Streaming output buffered until complete instead of flushing each chunk — replaced SDK's `PrintingCallbackHandler` with `_StreamingCallback` that flushes immediately, skips tool headers (observer handles those), and emits exactly one trailing newline on complete instead of two
- Streamed output wrote to raw stdout instead of the session's console sink — `create_agent()` now threads the console's file into the callback so both CLI paths (interactive and scripted run) write to the same destination

## [1.2.1] - 2026-03-15

### Fixed

- Agent responses used emojis, bold markdown, tables, and verbose motivational filler — added strict tone rules to system prompt: no emojis (even if context contains them), no markdown formatting (terminal doesn't render it), plain short sentences only
- Agent recommended unavailable movies from stale context without searching — context flagged as unverified memory; time-sensitive queries now require web_search
- Exit summarizer stored transient data (recommendations, weather, prices, news) in CONTEXT.md causing stale answers — updated both summarizer prompts to exclude time-sensitive data and keep only durable facts
- Parallel tool call trace output interleaved — `✓`/`✗` result lines now include the tool name so each completion can be matched to its call

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
- DuckDuckGo web search via `ddgs` — replaces placeholder with live search returning top 5 results (title, URL, snippet). Lazy import, no try/except (errors propagate to observer)
- System prompt guidance to prefer web_search snippets over http_request fetches, reducing unnecessary tool calls
- 78 new tests for v1.2 features (343 total)

### Fixed

- `blueclaw run` blocked for several seconds after printing "Done" — synchronous context update replaced with background thread (`BackgroundContextUpdater`). "Done" prints immediately; context update completes before process exits via `updater.wait()`
- Scripted mode let `http_request` run against non-allowlisted domains (tool failed silently, agent retried 10+ domains). Now cancels the tool call with a clear message directing the agent to use search snippets
- Background context update failures printed visible `WARNING` to stderr — downgraded to debug level

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
