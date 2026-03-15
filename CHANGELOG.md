# Changelog

All notable changes to blueclaw will be documented in this file.

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
