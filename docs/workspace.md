# Workspace layout

Everything BlueClaw writes lives under `~/blueclaw/`. This page is the single source of truth for that tree — what each path is, who writes it, when, and what reads it. Feature docs (`api.md`, `tracing.md`, `bridges/telegram.md`, etc.) reference paths but don't try to be the layout reference.

## Top-level tree

```
~/blueclaw/
├── workspace/                  # default workspace (terminal + HTTP)
├── chats/<chat_id>/            # per-Telegram-chat workspace (one per chat)
├── skills/                     # user-installed skills (cross-workspace)
└── test-runs/<invocation-ts>/  # eval scratch (created by `blueclaw test`)
```

A "workspace" is just a directory. `~/blueclaw/workspace/` and each `~/blueclaw/chats/<chat_id>/` have identical internal structure; they're each their own isolated sandbox root with their own CONTEXT, traces, captures, and session state. The terminal and HTTP adapters share the default workspace; the Telegram bridge mints a fresh per-chat one on first message.

`~/blueclaw/skills/` sits at the top level (NOT inside any workspace) and holds user-installed skills shared across all workspaces. Created on demand by `blueclaw.launcher`; populated by `blueclaw skill install …`. The Docker sandbox bind-mounts it read-only into the container.

## Inside any workspace

```
<workspace_root>/
├── CONTEXT.md                  # persistent agent knowledge (user-editable)
├── SOUL.md                     # personality/voice tuning (user-editable, optional)
├── blueclaw.yaml               # config (only when `blueclaw init` ran from the workspace dir; otherwise lives at project root or via BLUECLAW_CONFIG)
├── <files the agent created>   # arbitrary — agent operates inside this root
└── .blueclaw/                  # bookkeeping; everything below is managed by BlueClaw
    ├── history.jsonl           # append-only run log (RunRecord per line)
    ├── last_turn.md            # last assistant response (shown on next interactive start)
    ├── traces/
    │   └── <run_id>.json       # RunTrace per agent run (structured timeline)
    ├── conversations/
    │   └── <cid>/
    │       ├── session_<cid>/  # Strands FileSessionManager state (HTTP, Telegram)
    │       ├── turns/
    │       │   └── turn-NNN/
    │       │       ├── response.txt    # raw assistant text for this turn
    │       │       └── messages.json   # full message list at end of turn
    │       └── uploads/        # files attached to API requests (HTTP only)
    ├── uploads_tmp/            # staging dir for in-progress HTTP uploads (not cid-keyed)
    └── .migrated-v1            # one-shot migration sentinel (do not delete)
```

## Per-file responsibility

| Path | Type | Writer | Reader | Lifetime |
|---|---|---|---|---|
| `CONTEXT.md` | markdown | session.py at session end; user manually | session.py at session start; user | persistent |
| `SOUL.md` | markdown | user (optional, created manually) | session.py at session start | persistent |
| `blueclaw.yaml` | YAML | `blueclaw init` (cwd-anchored — lands here when init ran from inside the workspace) | `cli.py` `_config_path()`; overridable via `BLUECLAW_CONFIG` env | persistent |
| `.blueclaw/history.jsonl` | JSONL | observer.py per run | `blueclaw history`; lessons.py | append-only, persistent |
| `.blueclaw/last_turn.md` | markdown | session.py per turn | `blueclaw` startup banner | overwritten per turn |
| `.blueclaw/traces/<run_id>.json` | JSON | adapter after `finalize` | `blueclaw trace *`; `web.py` (`/api/traces`) | retained until `blueclaw trace purge` |
| `.blueclaw/conversations/<cid>/turns/turn-NNN/{response,messages}` | text + JSON | `runner._write_capture_artifacts` per turn | `web.py` (`/api/turns/...`); dashboard preview chip | not pruned automatically (operator action only) |
| `.blueclaw/conversations/<cid>/session_<cid>/` | Strands SDK state | `FileSessionManager` per turn | `FileSessionManager` on next turn | retained until `purge_old_sessions` (`trace_retention_days`) |
| `.blueclaw/conversations/<cid>/uploads/` | binary blobs | `server.py` `POST /upload` | `server.py` `_resolve_attachments` | retained per `UploadStore` policy |
| `.blueclaw/uploads_tmp/` | binary blobs (staging) | `server.py` during upload | `server.py` on finalize | ephemeral; cleared after upload completes or fails |
| `.blueclaw/.migrated-v1` | sentinel file | `workspace.py` migration | `workspace.py` on load (skip if present) | persistent; do not delete |

## What `<cid>` means per adapter

The `<cid>` segment in `conversations/<cid>/` is the conversation/session identifier. All per-conversation state (session, turns, uploads) lives inside this single directory. Format varies per adapter:

| Adapter | `<cid>` format | Example |
|---|---|---|
| Terminal | `YYYYMMDD-HHMMSS-xxxx` minted per process at session start | `20260518-143022-a1b2` |
| HTTP | client-supplied `conversation_id` (validated `^[a-zA-Z0-9_-]{1,64}$`) | `my-chat-2026` |
| Telegram | `str(chat_id)` (the integer Telegram chat ID) | `487341290` |

Telegram's case produces a cosmetically redundant path (the chat ID appears both in the workspace root `chats/<chat_id>/` and again as `<cid>` inside it). This is intentional — uniform layout means tooling can assume one shape everywhere.

## Retention

- **`CONTEXT.md`, `SOUL.md`, `history.jsonl`** — never auto-deleted; persistent across runs.
- **`last_turn.md`** — overwritten every turn; not really retained, just refreshed.
- **`traces/`** — purged by `purge_old_traces(trace_retention_days)` (default 30 days, configurable in `blueclaw.yaml`). Called automatically on `blueclaw serve` startup and on `blueclaw trace purge`.
- **`conversations/<cid>/session_<cid>/`** — purged by `purge_old_sessions(trace_retention_days)` on the same schedule.
- **`conversations/<cid>/turns/`** — **no automatic retention.** Operators delete `.blueclaw/conversations/<cid>/` manually to reclaim disk. The dashboard renders a "captures pruned" badge when a trace's `capture_path` points at a directory that no longer exists.
- **`conversations/<cid>/uploads/`** — retention managed by `UploadStore`.
- **`uploads_tmp/`** — ephemeral; cleaned up by the server after each upload completes or fails.

## Eval scratch (`~/blueclaw/test-runs/`)

`blueclaw test` writes a separate tree that does NOT live inside a workspace:

```
~/blueclaw/test-runs/<invocation-ts>/
├── invocation.json             # per-invocation metadata (case_idx, run_idx)
└── case-<NNN>/
    └── run-<NNN>/
        ├── response.txt
        └── messages.json
```

`<invocation-ts>` is the timestamp the eval started; each test invocation gets its own scratch dir under it. Each test case gets a `case-<NNN>/` subdir, and each retry within a case gets a `run-<NNN>/` subdir holding the captured artifacts. The agent's working files live under a separate workspace dir (`case-<NNN>/`) and are deleted at the end unless `--keep-workspace` is passed. `response.txt` and `messages.json` always persist regardless of `--keep-workspace`. See `docs/testing.md` for the full eval story.

## Docker sandbox

When `sandbox.mode: docker` is configured, the host workspace is bind-mounted into the container alongside several other paths (skills, chats root, config, tmpfs). See `docs/sandbox.md` for the full mount table — `workspace.md` only describes the host-side layout.

## What's NOT in the workspace

Some BlueClaw state lives outside the workspace tree:

- **Config:** `blueclaw.yaml` is resolved relative to the current working directory (override with `BLUECLAW_CONFIG`). It can sit anywhere — alongside your project source, inside the workspace, or at `~/blueclaw/workspace/blueclaw.yaml`. There is no required location.
- **Project skills:** `.claude/skills/` (project-local, alongside source code). Distinct from `~/blueclaw/skills/` (user-installed, cross-workspace, top-level).
- **Trace UI cache:** none — `blueclaw trace ui` is stateless; everything it shows comes from the workspaces it was pointed at.

## Multi-workspace queries

`blueclaw history --all-chats`, `blueclaw trace ui --all-chats`, and similar commands aggregate across the default workspace AND every directory under `~/blueclaw/chats/`. The aggregation happens at read time — workspaces are never merged on disk.
