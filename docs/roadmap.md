# blueclaw Roadmap

> Milestones from v1 (terminal agent) → v1.1 (trace tooling) → v1.2 (webhook gateway) → v2 (multi-channel system).
> Derived from [design.md](design.md). Each milestone ships independently.
> **Current:** v1 complete. v1.1 next.

---

## v1 — Terminal Agent (Foundation)

**Goal:** A working interactive terminal agent powered by Strands SDK. Small enough to read in one sitting.

| Deliverable | File(s) | Notes |
|---|---|---|
| Interactive terminal session with Rich welcome banner + pixel art mascot | `cli.py` | Typer entrypoint, prompt_toolkit input, Rich output ✅ |
| Strands Agent as the core loop | `session.py` | Loads CONTEXT.md + history.jsonl, builds Agent, runs chat loop ✅ |
| Model-agnostic support (Claude, Ollama, Gemini) | `session.py`, `models.py` | Factory for AnthropicModel / OllamaModel / LiteLLMModel via config ✅ |
| Persistent memory: `CONTEXT.md` + `history.jsonl` | `session.py` | Read on start, write on end; JSONL append-only audit trail ✅ |
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
**Actual lines:** 1,482
**Dependencies:** strands-agents, strands-agents-tools, typer, rich, prompt-toolkit, pydantic, pyyaml + dev: pytest, pytest-mock, flake8, black

---

## v1.1 — Trace Tooling

**Goal:** Make BlueClaw runs debuggable after the fact. Extend the structured trace infrastructure with CLI tools for explanation, comparison, and visualization.

| Deliverable | File(s) | Status | Notes |
|---|---|---|---|
| `blueclaw trace explain <run_id>` | `cli.py`, `session.py` | ⬚ | Feed recorded trace to LLM for post-hoc explanation. Label as "post-hoc explanation", not actual chain-of-thought. |
| Execution graph view | `cli.py` | ⬚ | Tree rendering of tool call sequences (e.g. search → fetch → fetch → summarize) |
| `blueclaw trace diff <id1> <id2>` | `cli.py` | ⬚ | Side-by-side comparison of two runs (steps, timing, cost) |
| `blueclaw trace replay <run_id>` | `cli.py` | ⬚ | Step-through viewer — walk through recorded trace interactively |

**Priority:** `trace explain` is highest-value — delivers the "agent you can actually debug" narrative with one command.

**Constraint:** Must stay within the 1,500-line ceiling. Current codebase is ~1,395 lines (~105 remaining). Some features may need to wait for refactoring headroom or be implemented as skills.

---

## v1.2 — Webhook API (Channel Gateway)

**Goal:** A single FastAPI endpoint that accepts messages and returns responses. Unlocks messaging channels without building a channel registry, message queue, or database.

### v1.2.0 — Stateless Webhook

| Deliverable | File(s) | Status | Notes |
|---|---|---|---|
| `POST /message` endpoint + `GET /health` | `server.py` (new, ~100 lines) | ⬚ | Reuses same `session.py` Agent as terminal mode |
| `blueclaw serve` command | `cli.py` (+10 lines) | ⬚ | Starts uvicorn on localhost:8420 |
| Quiet observer mode for webhook | `observer.py` (+10 lines) | ⬚ | Suppress Rich console output in API mode |
| `ServerConfig`, `MessageRequest`, `MessageResponse` models | `models.py` (+15 lines) | ⬚ | Forward-compatible API contract (channel, conversation_id, sender fields logged but not routed) |

**Core files:** 7 (+server.py)
**Estimated lines:** ~1,600
**New dependencies:** fastapi, uvicorn (optional)

### v1.2.1 — Stateful Conversations

| Deliverable | File(s) | Status | Notes |
|---|---|---|---|
| Per-conversation persistence via Strands `FileSessionManager` | `server.py`, `session.py` | ⬚ | `build_webhook_agent(conversation_id, session_manager)` helper |
| `conversation_id` tracking | `server.py` | ⬚ | Missing ID → random one-shot ID (preserves stateless behavior) |

### Channel Adapters (Skills, Not Core)

Channel adapters are skills that translate platform webhooks to `POST /message`:

- `/add-slack` — Slack webhook adapter ⬚
- `/add-discord` — Discord bot adapter ⬚
- `/add-telegram` — Telegram bot adapter ⬚

The core never imports platform SDKs. Adapters are thin translators.

⚠️ *[Architectural note: In v1.2, adapter skills are run-once transformers (generate code). In v2, adapters must persistently register with `ChannelRegistry` — the transition from "skill that generates code" to "loaded module that registers at startup" needs clarification in the design. The design.md shows adapters importing and registering at module load time, which is a different pattern than skill-based code generation.]*

---

## v2 — Full Multi-Channel System

**Goal:** Production-grade multi-channel agent with conversation routing, sender auth, concurrent conversations, typing indicators, and optional Docker sandbox.

### v2-alpha — Persistence + Concurrency

| Deliverable | File(s) | Status | Notes |
|---|---|---|---|
| SQLite message persistence (conversations, messages, allowed_senders) | `db.py` (new, ~150 lines) | ⬚ | Python stdlib `sqlite3`, zero new deps |
| Conversation queue with per-conversation serialization | `queue.py` (new, ~80 lines) | ⬚ | `max_concurrent` config (default: 5) |
| Database sink for observer | `observer.py` (+24 lines) | ⬚ | `DatabaseSink` alternative to console output |
| Workspace db_path + sessions_dir | `workspace.py` (mod) | ⬚ | New properties for structured storage |

### v2-beta — Channel Registry + Typing

| Deliverable | File(s) | Status | Notes |
|---|---|---|---|
| `ChannelAdapter` protocol + `ChannelRegistry` | `channels.py` (new, ~120 lines) | ⬚ | Protocol-based — adapters implement methods, no base class inheritance |
| Server routes through registry + queue | `server.py` (expand to ~180 lines) | ⬚ | Sender validation, conversation routing, `/conversations` API endpoints |
| Typing indicators via `BeforeToolCallEvent` hook | `observer.py` (mod) | ⬚ | `adapter.send_typing()` on tool call start |
| Channel config models | `models.py` (+18 lines) | ⬚ | `ConversationRecord`, `ChannelConfig`, `SenderPolicy` |

### v2 — Daemon + CLI

| Deliverable | File(s) | Status | Notes |
|---|---|---|---|
| `blueclaw serve --install` (launchd/systemd) | `cli.py` (+19 lines) | ⬚ | Generate OS service config, print instructions |
| `blueclaw serve --status` | `cli.py` | ⬚ | Check daemon status |
| `blueclaw channels` command | `cli.py` | ⬚ | List registered channel adapters |
| `blueclaw conversations` command | `cli.py` | ⬚ | List/query conversations from SQLite |

### v2-rc — Docker Sandbox + Skill Loading

| Deliverable | File(s) | Status | Notes |
|---|---|---|---|
| Optional Docker workspace isolation | `workspace.py` (+73 lines) | ⬚ | `sandbox: docker` config, volume mount, resource caps |
| Docker `execute()` method | `workspace.py` | ⬚ | Routes shell commands through `docker run` when enabled |
| Graceful fallback to app-level sandbox | `workspace.py` | ⬚ | If Docker unavailable, warn and use `sandbox: app` |
| Enhanced progressive skill loading | `session.py` (+15 lines) | ⬚ | Skill index scanner, critical as skill ecosystem grows |

**Core files:** 10
**Estimated lines:** ~2,100
**Dependencies:** 7 + 2 optional (fastapi, uvicorn); sqlite3 is stdlib

---

## Persistence Layers (v2 Complete)

| Store | Purpose | Reader | Lifetime | Status |
|---|---|---|---|---|
| `CONTEXT.md` | Agent's semantic memory | Agent + human | Per-workspace, human-editable | ✅ |
| `history.jsonl` | Run audit log | `blueclaw history` | Append-only | ✅ |
| `traces/*.json` | Structured execution traces | `blueclaw trace` | Per-run | ✅ |
| `FileSessionManager` | Strands conversation state | Agent (context restore) | Per-conversation | ⬚ |
| SQLite (`blueclaw.db`) | Messages, routing, sender auth | Server, API, CLI | Per-workspace | ⬚ |

---

## Migration Path

- **v1 → v1.1:** Extend trace CLI. Existing terminal mode unchanged.
- **v1.1 → v1.2:** Add `server.py`. Existing terminal mode unchanged.
- **v1.2 → v2:** Existing skill adapters work unchanged (same POST body — v2 activates fields v1.2 ignored). `CONTEXT.md` and `history.jsonl` untouched. `blueclaw.yaml` gains optional sections with sensible defaults.

---

## Explicitly Deferred (v3+)

| Feature | Reason |
|---|---|
| Web UI dashboard | Complexity vs. value for terminal-native users |
| Task scheduling | Can be a skill, not core |
| Multi-agent collaboration | Strands supports it — add when there's a real use case |
| Browser automation | Can be an MCP server, not core |
| Network-level domain isolation | Requires Docker proxy; `--network=host` is v2's tradeoff |
| Persistent container (`docker exec` reuse) | Optimization for v2's per-command container spawn latency |

---

## Complexity Budget

| Metric | v1 | v1.1 | v1.2 | v2 |
|---|---|---|---|---|
| Core files | 6 | 6 | 7 | 10 |
| Dependencies | 7 | 7 | 7 + 2 optional | 7 + 2 optional |
| Lines (actual/est.) | 1,482 | ~1,500 | ~1,600 | ~2,100 |
| One-sitting readable? | Yes | Yes | Yes | Stretch |

---

*Derived from [design.md](design.md) — last updated 2026-03-15*
