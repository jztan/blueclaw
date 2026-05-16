# Changelog

All notable changes to blueclaw will be documented in this file.

## [2.5.0] - 2026-05-16
### Added
- **Telegram bridge.** New `blueclaw telegram` subcommand exposes blueclaw to
  Telegram with per-chat workspaces under `~/blueclaw/chats/<chat_id>/`,
  Strands `FileSessionManager`-backed conversation continuity, allowlist-enforced
  authorization (empty allowlist refuses everyone), `/whoami` `/reset` `/forget`
  commands, long-polling by default and webhook mode opt-in, and a `--echo`
  smoke-test mode. Each turn is persisted via `build_trace_and_record` →
  `write_trace` + `append_history` with `source="telegram"`. Install via
  `pip install -e ".[telegram]"`. See `docs/bridges/telegram.md`.
- **`blueclaw history --chat <id>` / `--all-chats`.** Inspect per-chat
  Telegram history without changing directory; `--all-chats` aggregates the
  default workspace with every `~/blueclaw/chats/<id>/`, labeling each row.
- **Telegram bridge: empty-reply fallback.** Small Ollama models occasionally
  emit only a tool-use block with no synthesized text. Previously this crashed
  the handler with Telegram's `BadRequest: Message text is empty`. The bridge
  now substitutes a friendly fallback (`(no reply — the model produced empty
  output. Try rephrasing, /reset, or a stronger model.)`) and skips empty chunks.
- **Sandbox: chats-root mount widened.** `~/blueclaw/chats/` is now bind-mounted
  into the container whenever it exists on the host, not just for the
  `telegram` subcommand. This lets `blueclaw trace ui --all-chats` and
  `blueclaw history --all-chats` see per-chat data when running under
  `sandbox.mode=docker`.
- **Dashboard: strict-mode regression fixed.** The multi-workspace patch had
  reassigned `function fetchJSON(...)`, which is a SyntaxError under the
  dashboard's `'use strict'` directive and broke the entire SPA. Replaced
  with an `apiFetch()` wrapper used at every `/api/*` call site.
- **Multi-workspace trace coverage.** Every `blueclaw trace *` reader now
  accepts `--chat <id>` (single Telegram chat) and, where the union makes
  sense (`list`, `stats`, `purge`, `ui`), `--all-chats`. Single-target
  `trace show / explain / graph / replay / timeline / diff` auto-scan
  workspaces and print a disambiguation hint on collision. The web
  dashboard (`blueclaw trace ui`) gains a workspace dropdown (with `All
  workspaces` when more than one exists), a `Source` column in the trace
  list, and a per-source breakdown table in the stats view. New
  `GET /api/workspaces` endpoint; existing `/api/traces`, `/api/traces/{id}`,
  `/api/stats` accept `?workspace=<key>` (`all` unions). Writers
  unchanged.
- **SOUL.md identity file.** Optional `<workspace>/SOUL.md` holds the agent's
  persona/voice (personality, values, communication style) separately from
  `CONTEXT.md` (which holds factual memory). When present, it is loaded as the
  `## Identity` section of the system prompt before `## Persistent Context`.
  `blueclaw init` writes a default template; the file is human-managed
  (no auto-writes) and re-read every turn so edits are picked up live.
- **Docker sandbox.** Opt-in via `sandbox.mode: docker` in `blueclaw.yaml`. Runs the
  entire agent process inside a short-lived container with the workspace bind-mounted,
  read-only root FS, no-new-privileges, all capabilities dropped, and configurable
  resource caps. Launcher transparently `execvp`s into `docker run` while keeping
  the user's TTY and signal forwarding intact. See `docs/sandbox.md`.
- **Layered env composition** for the sandbox: built-in allowlist →
  `~/blueclaw/.env` → `<project>/.env` (same file `python-dotenv` already loads on
  the host) → `<project>/.env.docker` → YAML `extra_env`. `blueclaw init` adds
  dotenv files to `.gitignore`.
- **`blueclaw sandbox build` / `blueclaw sandbox doctor`** CLI commands.
- **Visible launch indicator:** the launcher prints
  `→ blueclaw sandbox: docker (<image-tag>)` to stderr when it re-execs into the
  container, so users can confirm the sandbox fired.
- **Editable-install detection** (PEP 610): editable installs build `dev-<sha>`
  images and overlay the source tree at `/opt/blueclaw-src` via `PYTHONPATH`.
- **Trace metadata** records `sandbox.mode/image/image_digest/fallback_reason` on
  every `TraceStep`.
- **Recursion guard** for the in-container blueclaw: when
  `BLUECLAW_SANDBOX_MODE=docker` is set, the launcher hook short-circuits so the
  container never tries to launch another container.

### Fixed
- **Launcher hook misfired under bare `pytest`.** `_maybe_execvp_into_docker`
  inspected `sys.argv` directly to route subcommands. Under bare `pytest` from a
  project with `sandbox.mode: docker` in `blueclaw.yaml`, `sys.argv=['pytest']`
  → `normalize_subcommand` returned `""` (the interactive-mode marker, which is
  in `_CONTAINER_COMMANDS`) → the hook tried to re-exec the test process into
  docker, causing every `runner.invoke(app, [])` in `tests/test_cli.py` to fail
  with `exit_code=2`. Added a host-program guard: the hook returns early when
  `os.path.basename(sys.argv[0]) != "blueclaw"`. CI didn't catch it because
  `.github/workflows/ci.yml` invoked `pytest tests/` (where `sys.argv[1]="tests"`
  isn't sandbox-routed) and ran in a clean checkout without `blueclaw.yaml`.
- **Test invocation parity with CI.** Added `testpaths = ["tests"]` to
  `[tool.pytest.ini_options]` and dropped the `tests/` arg from the CI test
  step so bare `pytest` and CI collect identically.
- **`blueclaw sandbox build` accumulated dev images.** Editable installs tag
  images as `blueclaw/runtime:dev-<short-sha>`, so every commit produced a new
  tag and the previous one lingered on disk. `sandbox build` now sweeps stale
  `dev-*` tags after a successful build so it behaves like an overwrite.
- **Spurious "unable to find previously injected skills XML" warning** on every
  resumed turn under `blueclaw serve`. `FileSessionManager` rehydrated the
  Strands skills plugin's `last_injected_xml` state from disk, but the system
  prompt was rebuilt fresh each turn, so the plugin couldn't match its prior
  injection and warned (harmlessly) before re-appending. `create_agent` now
  clears the `agent_skills` state key after construction when a session manager
  is attached, so the plugin treats the next invocation as a first injection.
- **Launcher hook fall-through.** `_maybe_execvp_into_docker` now raises
  `SystemExit(0)` after `os.execvp` so the function cannot continue into the
  in-process subcommand if execvp ever returns. Fixes a CI hang where the
  `test_docker_mode_trace_ui_calls_execvp` test mocked `os.execvp`, fell
  through to typer's subcommand dispatch, and blocked forever inside a real
  `uvicorn.run`.

### Changed
- **Sandbox auto-host for Ollama.** When `provider: ollama` and
  `sandbox.mode: docker`, the launcher now defaults `OLLAMA_HOST` to
  `http://host.docker.internal:11434` (unless the user set it explicitly) and
  adds `--add-host=host.docker.internal:host-gateway` to the `docker run` argv.
  Containers previously hit `localhost:11434` — which is the container itself
  — and surfaced an opaque "All connection attempts failed" mid-stream.
  Requires the host Ollama daemon to bind a reachable interface
  (`OLLAMA_HOST=0.0.0.0:11434 ollama serve`).
- **`blueclaw serve` updates CONTEXT.md per turn.** Matches CLI behavior: each
  successful `/message` or `/message/stream` turn calls
  `BackgroundContextUpdater.trigger(agent)`, which spawns one background
  summarization thread. Concurrent triggers are coalesced (the updater is a
  no-op while a previous one is in flight), so multiple conversations writing
  the same global file can't race. The Starlette lifespan handler now waits
  on the last in-flight thread (up to 15s) so Ctrl+C never truncates a write.
- **Runtime image** installs all four model-provider extras (`anthropic`,
  `ollama`, `openai`, `gemini`) so the image works for any configured provider
  without a rebuild.
- **HOME tmpfs:** the sandbox now mounts ephemeral tmpfs at
  `/home/blueclaw/.cache` (256 MB), `/home/blueclaw/.config` (32 MB),
  `/home/blueclaw/.local` (64 MB) so XDG-respecting tools (pdf-mcp cache, pip
  cache, HuggingFace cache, etc.) work under `--read-only` root.
- **In-container bind defaults to 0.0.0.0** for `blueclaw serve` and
  `blueclaw trace ui` when running under the sandbox, so `--publish <port>:<port>`
  actually reaches the in-container server. Outside the sandbox, the bind stays
  on `127.0.0.1` (unchanged).
- **Config path resolution** honors `BLUECLAW_CONFIG` env var, set by the launcher
  to the bind-mounted config path inside the container. The config is mounted
  *outside* the workspace mount (`/home/blueclaw/blueclaw.yaml`) because macOS
  Docker Desktop with VirtioFS refuses to bind-mount a file inside another
  bind-mount.

## [2.4.0] - 2026-05-16
### Added
- Skill packaging (v2.4): blueclaw now adopts the AgentSkills.io standard.
  Skills are directories containing a `SKILL.md` (YAML frontmatter +
  markdown body), loaded via Strands' `AgentSkills` plugin (1.30+).
- `blueclaw skill install` (local path or git URL with optional
  `#subdir`), `uninstall`, `list`, `show` subcommands.
- `blueclaw skill install` accepts a direct HTTPS URL pointing at a raw
  `SKILL.md` (in addition to local paths and git URLs).
- Project-vs-global scope: skills under `~/blueclaw/skills/` and
  `<project>/.blueclaw/skills/` are both discovered, with project
  precedence on name collision.

### Changed
- Single-file `<name>.md` skill format is no longer supported. Migrate by
  promoting each file to a directory containing a `SKILL.md` with YAML
  frontmatter (`name`, `description`).
- `pyproject.toml`: pin bumped to `strands-agents>=1.30.0`.
- CI now installs from a checked-in `uv.lock` via
  `uv sync --frozen --extra dev` (was `uv pip install -e .[dev]`), so
  builds are reproducible and dependency bumps require an explicit
  `uv lock` commit. Same change applied to the PyPI publish workflow.

### Fixed
- `tests/test_context.py::test_register_hooks_adds_callback` relaxed from
  `assert_called_once_with` to `assert_any_call` — newer
  `strands-agents` releases register an additional
  `BeforeModelCallEvent` callback on the parent
  `SummarizingConversationManager`, which previously broke CI on Python
  versions that resolved to a newer strands than the local pin.

## [2.3.0] - 2026-05-10
### Added
- `POST /upload` accepts multipart files (PDF, text, markdown, csv, json, png/jpeg/webp/gif, zip) up to 25 MB and returns a `file_id` scoped to a conversation. Uploads land under `<workspace>/.blueclaw/uploads/<conversation_id>/<file_id>`. Oversize requests are rejected by a `Content-Length` pre-check before the body is read; the same cap is also enforced during streaming write as a defense in depth.
- `MessageRequest.file_ids` (max 10 per request) lets clients reference uploaded files. The server resolves each id to its absolute path and prepends a system note to the agent prompt, so existing shell, pdf-mcp, and web tools can read attachments without provider-specific wiring.
- Playground gains a paperclip button, drag-and-drop, and removable attachment chips. Files upload immediately and are sent with the next message; chips clear after a successful send.
- Optional pip extras for provider SDKs: `blueclaw[anthropic]`, `blueclaw[ollama]`, `blueclaw[openai]`, `blueclaw[gemini]`. Strands lazy-imports these SDKs only when the matching provider is selected, so they no longer ship as runtime dependencies of the base install.
- Native vision support for image attachments. When a `file_id` resolves to a PNG/JPEG/GIF/WEBP, the server now passes the bytes to the agent as a Strands `image` content block instead of just a path note, so vision-capable models (Anthropic, OpenAI, Bedrock, Ollama vision tags) can actually see pixels. Non-image attachments keep the path-prefix flow.
- `@<path>` attachments in the CLI. Both `blueclaw` (interactive) and `blueclaw run "..."` now scan user input for whitespace-delimited `@<path>` tokens, resolve each against the cwd (or the absolute path), and route the file through the same image-vs-path logic as the HTTP API. Combined with the macOS/iTerm shift+drag behavior — which inserts a file's absolute path at the cursor — this gives CLI users a Claude-Code-style attach UX. Tokens that don't resolve (e.g. `@username`, `user@example.com`) pass through unchanged; tokens that *look* like paths but don't resolve emit a yellow "could not attach" warning so typos are immediately diagnosable.
- Bare absolute paths and quoted paths (single or double) auto-attach without an `@`-prefix. Pasting `/Users/foo/pic.png` or `'/Users/foo/with space.pdf'` into the prompt is enough — useful for shift+drag flows that don't add the `@`. Bare relative paths (`pic.png`) still require the explicit `@` to avoid attaching casual mentions.

### Fixed
- Inline images larger than ~3.5 MB raw (~5 MB base64) are now rejected with a clear "image too large for inline attachment" message instead of letting Anthropic return a 400 mid-stream. Affects both the CLI `@<path>` flow and HTTP `file_ids`.
- The interactive REPL no longer exits silently when the agent or attachment-builder raises an exception. Per-turn errors are printed in red and the loop continues; the outer "session error" guard also surfaces its message instead of swallowing it.

### Changed
- `Workspace.purge_old_sessions` now also removes the matching `uploads/<cid>` directory and any orphaned `tmp-*` upload directories older than `trace_retention_days`.

## [2.2.0] - 2026-05-10
### Added
- Stateful conversations: when `POST /message` (or `/message/stream`) supplies a `conversation_id`, history is persisted via Strands `FileSessionManager` under `<workspace>/.blueclaw/sessions/<id>/`. Subsequent requests with the same id replay prior turns. Omitting `conversation_id` keeps stateless behavior.
- `conversation_id` field on `RunTrace` and `RunRecord` (also exposed in `/api/traces` summary) so traces and history rows can be grouped by conversation.
- `GET /playground` — single-page chat UI bundled with `blueclaw serve` for manually exercising stateful + streaming conversations. Defaults its server URL to the current origin; bearer token entered in the sidebar. Unauthenticated like `/health`.
- `docs/models.md` — detailed model-support reference: per-provider `blueclaw.yaml` and `.env` samples (Anthropic, OpenAI, Ollama with tool-calling shortlist, LiteLLM for Gemini/Bedrock), CLI override precedence, cost tracking, and "adding a new provider" recipe.
- Prompt-cache token billing: `calculate_cost()` accepts optional `cache_read_tokens` / `cache_write_tokens` and bills them at `0.1×` / `1.25×` of the input rate (Anthropic prompt-caching multipliers). All three call sites (`session.py`, `server.py`, `testing.py`) pull `cacheReadInputTokens` / `cacheWriteInputTokens` from `accumulated_usage`, avoiding cost overstatement on cache-heavy sessions.
- `PRICING_UPDATED` constant in `blueclaw/models.py` — explicit "last reviewed" date for the pricing table; bump it whenever `MODEL_PRICING_PER_M` is edited.

### Changed
- `build_trace_and_record(...)` accepts an optional `conversation_id` kwarg.
- `build_system_prompt(...)` accepts `include_history`. `create_agent` automatically passes `False` whenever a `session_manager` is attached, so the system prompt no longer narrates a "Recent History" recap that overlaps with the messages the session manager replays. Stops the model from prefacing each stateful reply with a conversation summary.
- `build_system_prompt(...)` accepts `channel` ("terminal" or "api"). `create_agent(channel=...)` threads it through; `blueclaw serve` passes `"api"` so HTTP responses follow chat-client tone rules ("answer only the new question, do not recap, no terminal-only constraints") instead of the CLI's strict plain-text rules. Fixes drift where stateful API replies grew progressively chattier and recapped earlier turns once the model had its own markdown-formatted prior messages replayed back to it.
- Pricing table renamed to `MODEL_PRICING_PER_M` and switched from per-1k to per-1M token units to match Anthropic's pricing page 1:1 (e.g. Sonnet input is now `3.0` rather than `0.003`). Old `MODEL_PRICING` name kept as an alias for backward compatibility.

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
