# Changelog

All notable changes to blueclaw will be documented in this file.

## [1.2.3] - 2026-03-15

### Fixed

- CI test failures for `TestBuildModel` (anthropic, litellm, openai) — `patch("strands.models.XModel")` triggered lazy `__getattr__` which imported real SDK packages not installed in CI. Replaced with `patch.dict` on module `__dict__` to bypass lazy imports
- 7 flake8 E501 line-too-long violations in `session.py` system prompt strings

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
