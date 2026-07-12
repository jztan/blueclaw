<p align="center">
  <img src="https://raw.githubusercontent.com/jztan/blueclaw/master/blueclaw-logo.PNG" alt="BlueClaw" width="400">
</p>

<p align="center">
  <strong>Understand, debug, and control AI agent behavior.</strong><br>
  Structured tracing, context management, and reproducible runs — all from the terminal.
</p>

<p align="center">
  <a href="#quickstart">Quickstart</a> &middot;
  <a href="#features">Features</a> &middot;
  <a href="#model-support">Models</a> &middot;
  <a href="#configuration">Configuration</a> &middot;
  <a href="#roadmap">Roadmap</a> &middot;
  <a href="#contributing">Contributing</a> &middot;
  <a href="#license">License</a>
</p>

<p align="center">
  <a href="https://pypi.org/project/blueclaw/"><img src="https://img.shields.io/pypi/v/blueclaw.svg" alt="PyPI Version"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/jztan/blueclaw.svg" alt="License"></a>
  <a href="https://pypi.org/project/blueclaw/"><img src="https://img.shields.io/pypi/pyversions/blueclaw.svg" alt="Python Version"></a>
  <a href="https://github.com/jztan/blueclaw/issues"><img src="https://img.shields.io/github/issues/jztan/blueclaw.svg" alt="GitHub Issues"></a>
  <a href="https://github.com/jztan/blueclaw/actions/workflows/ci.yml"><img src="https://github.com/jztan/blueclaw/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://pepy.tech/project/blueclaw"><img src="https://pepy.tech/badge/blueclaw" alt="Downloads"></a>
  <a href="https://github.com/cagataycali/awesome-strands-agents"><img src="https://img.shields.io/badge/Awesome-Strands%20Agents-00FF77?style=flat-square&logo=data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMjkwIiBoZWlnaHQ9IjQ2MyIgdmlld0JveD0iMCAwIDI5MCA0NjMiIGZpbGw9Im5vbmUiIHhtbG5zPSJodHRwOi8vd3d3LnczLm9yZy8yMDAwL3N2ZyI+CjxwYXRoIGQ9Ik05Ny4yOTAyIDUyLjc4ODRDODUuMDY3NCA0OS4xNjY3IDcyLjIyMzQgNTYuMTM4OSA2OC42MDE3IDY4LjM2MTZDNjQuOTgwMSA4MC41ODQzIDcxLjk1MjQgOTMuNDI4MyA4NC4xNzQ5IDk3LjA1MDFMMjM1LjExNyAxMzkuNzc1QzI0NS4yMjMgMTQyLjc2OSAyNDYuMzU3IDE1Ni42MjggMjM2Ljg3NCAxNjEuMjI2TDMyLjU0NiAyNjAuMjkxQy0xNC45NDM5IDI4My4zMTYgLTkuMTYxMDcgMzUyLjc0IDQxLjQ4MzUgMzY3LjU5MUwxODkuNTUxIDQxMS4wMDlMMTkwLjEyNSA0MTEuMTY5QzIwMi4xODMgNDE0LjM3NiAyMTQuNjY1IDQwNy4zOTYgMjE4LjE5NiAzOTUuMzU1QzIyMS43ODQgMzgzLjEyMiAyMTQuNzc0IDM3MC4yOTYgMjAyLjU0MSAzNjYuNzA5TDU0LjQ3MzggMzIzLjI5MUM0NC4zNDQ3IDMyMC4zMjEgNDMuMTg3OSAzMDYuNDM2IDUyLjY4NTcgMzAxLjgzMUwyNTcuMDE0IDIwMi43NjZDMzA0LjQzMiAxNzkuNzc2IDI5OC43NTggMTEwLjQ4MyAyNDguMjMzIDk1LjUxMkw5Ny4yOTAyIDUyLjc4ODRaIiBmaWxsPSIjRkZGRkZGIi8+CjxwYXRoIGQ9Ik0yNTkuMTQ3IDAuOTgxODEyQzI3MS4zODkgLTIuNTc0OTggMjg0LjE5NyA0LjQ2NTcxIDI4Ny43NTQgMTYuNzA3NEMyOTEuMzExIDI4Ljk0OTIgMjg0LjI3IDQxLjc1NyAyNzIuMDI4IDQ1LjMxMzhMNzEuMTcyNyAxMDMuNjcxQzQwLjcxNDIgMTEyLjUyMSAzNy4xOTc2IDE1NC4yNjIgNjUuNzQ1OSAxNjguMDgzTDI0MS4zNDMgMjUzLjA5M0MzMDcuODcyIDI4NS4zMDIgMjk5Ljc5NCAzODIuNTQ2IDIyOC44NjIgNDAzLjMzNkwzMC40MDQxIDQ2MS41MDJDMTguMTcwNyA0NjUuMDg4IDUuMzQ3MDggNDU4LjA3OCAxLjc2MTUzIDQ0NS44NDRDLTEuODIzOSA0MzMuNjExIDUuMTg2MzcgNDIwLjc4NyAxNy40MTk3IDQxNy4yMDJMMjE1Ljg3OCAzNTkuMDM1QzI0Ni4yNzcgMzUwLjEyNSAyNDkuNzM5IDMwOC40NDkgMjIxLjIyNiAyOTQuNjQ1TDQ1LjYyOTcgMjA5LjYzNUMtMjAuOTgzNCAxNzcuMzg2IC0xMi43NzcyIDc5Ljk4OTMgNTguMjkyOCA1OS4zNDAyTDI1OS4xNDcgMC45ODE4MTJaIiBmaWxsPSIjRkZGRkZGIi8+Cjwvc3ZnPgo=&logoColor=white" alt="Awesome Strands Agents"></a>
</p>

---

- **Structured traces** — every run writes a queryable JSON trace plus a per-turn event stream, all from the terminal
- **Regression testing** — define expected behavior in YAML and run it as CI (TAP/JUnit, Wilson CI scoring)
- **Context management** — observation masking keeps token cost low across long sessions without losing quality
- **HTTP API** — `blueclaw serve` exposes the agent with bearer auth, SSE streaming, stateful conversations, and file uploads
- **Telegram bridge** — talk to the agent from your phone; per-chat workspaces, allowlist-enforced
- **Extensible & model-agnostic** — package behavior as `SKILL.md` directories, isolate the whole process in Docker, run Claude / Ollama / OpenAI / Gemini

## Quickstart

```bash
pip install blueclaw
blueclaw init
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env
blueclaw
```

Install the extra for the model provider you want:

```bash
pip install "blueclaw[anthropic]"  # Claude (default)
pip install "blueclaw[ollama]"     # local models via Ollama
pip install "blueclaw[openai]"     # OpenAI
pip install "blueclaw[gemini]"     # Google Gemini (via LiteLLM)
```

Attach a file in one shot — `@<path>` or a bare absolute/quoted path both work:

```bash
blueclaw run "@~/Downloads/screenshot.png what is this?"
blueclaw run "'/Users/me/notes.pdf' summarize this"
```

## Features

### Tracing & Observability — [docs/tracing.md](docs/tracing.md)

Every turn produces a structured JSON trace plus a per-turn `events.jsonl` (tool calls, model invocations, context masking, lesson injection). Ten CLI commands inspect, compare, and replay runs; `blueclaw trace ui` opens a conversation-first browser dashboard (with optional `--live` streaming) on the same data.

```
$ blueclaw trace graph 20260315-054426

search for Python 3.13 new features
├── web_search (1ms) ✓  query: Python 3.13 new features
├── web_search (1ms) ✓  query: Python 3.13 new features list 2024
└── http_request (366ms) ✓  url: https://docs.python.org/3.13/whatsnew/3.13.html
```

`trace list` · `show` · `graph` · `timeline` · `diff` · `explain` · `replay` · `stats` · `ui` · `purge`. All accept `--chat <id>` to target one Telegram chat, and (where union makes sense) `--all-chats`.

### Regression Testing — [docs/testing.md](docs/testing.md)

Define expected behavior in YAML, run it as a CI test suite with TAP or JUnit output; multi-run Wilson CI scoring handles non-determinism. 11 deterministic assertions cover tools called, output content, file existence, cost, step count, duration, and tool order.

```bash
blueclaw test spec.yaml
blueclaw test spec.yaml --format junit -o results.xml
```

### Context Management

Tool outputs from older turns are automatically masked to keep token cost low across long sessions without losing model reasoning quality. A hybrid summarization mode is available for very long conversations.

### HTTP API — [docs/api.md](docs/api.md)

`blueclaw serve` exposes the agent over HTTP with bearer auth (`BLUECLAW_API_KEY`), body/timeout caps, and a configurable concurrency semaphore. `POST /message` returns a reply; `POST /message/stream` emits SSE token deltas; `POST /upload` attaches files (PDF, text, images with native vision) referenced by `file_id`. Per-`conversation_id` history persists via `FileSessionManager`, and every request writes a trace. A single-page chat UI ships at `GET /playground`.

```bash
blueclaw serve                          # http://127.0.0.1:8420
curl -X POST http://127.0.0.1:8420/message \
  -d '{"message": "what is in the workspace?"}' | jq .
```

### Telegram Bridge — [docs/bridges/telegram.md](docs/bridges/telegram.md)

Talk to blueclaw from your phone. Allowlist-enforced; each chat gets its own workspace under `~/blueclaw/chats/<chat_id>/` with its own `CONTEXT.md` and `history.jsonl`. Long-polling by default (no public URL needed); webhook mode opt-in.

```bash
pip install -e ".[telegram]"
export TELEGRAM_BOT_TOKEN=123456:abc...
blueclaw telegram                       # starts long-polling
blueclaw telegram --echo --allow 12345  # smoke test, no model calls
```

Commands: `/whoami`, `/start`, `/reset` (clears history), `/forget` (wipes both). Inspect per-chat history from the host with `blueclaw history --chat <id>` or `--all-chats`.

### Skills — [docs/skills.md](docs/skills.md)

Skills are directories containing a `SKILL.md` (YAML frontmatter + markdown body) that the agent loads on demand, built on the [AgentSkills.io](https://agentskills.io) standard. Install from a local path, git URL (with optional `#subdir`), or direct HTTPS to raw `SKILL.md`. Per-project skills under `<project>/.blueclaw/skills/` take precedence over user-global skills under `~/blueclaw/skills/`.

```bash
blueclaw skill install ./my-skill
blueclaw skill install https://github.com/u/repo.git#sub
blueclaw skill list
```

### Docker Sandbox — [docs/sandbox.md](docs/sandbox.md)

Opt-in container isolation for the entire agent process. With `sandbox.mode: docker` in `blueclaw.yaml`, blueclaw transparently re-execs into a short-lived container with the workspace bind-mounted, read-only root FS, `no-new-privileges`, all capabilities dropped, and configurable CPU / memory / pid caps. TTY and signals pass through; it falls back to in-process when Docker is unavailable. See the docs for image builds, network modes, and secret composition.

```bash
blueclaw sandbox build      # build the runtime image
blueclaw sandbox doctor     # diagnose docker + image state
```

## Model Support — [docs/models.md](docs/models.md)

```bash
blueclaw                                    # Anthropic (default)
blueclaw --model ollama/llama3.1:8b         # Ollama (local)
blueclaw --model openai/gpt-4.1-mini       # OpenAI
blueclaw --model litellm/gemini/gemini-2.0-flash  # Gemini via LiteLLM
```

Set API keys in `.env` (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, …).

## Configuration

`blueclaw.yaml` in your project root:

```yaml
model:
  provider: anthropic
  model_id: claude-sonnet-4-6

workspace:
  path: ~/blueclaw/workspace/
  trace_retention_days: 30

tools:
  - web
  - shell
  - pdf
  - mcp:http://localhost:8080/sse        # SSE MCP server (use mcp:<command> for stdio)

allowlist_domains:
  - github.com
  - docs.python.org
```

Drop a `SOUL.md` into your workspace to define the agent's persona and communication style — separate from `CONTEXT.md` (factual memory). `blueclaw init` writes a default template; edits are picked up on the next turn.

## Architecture

<p align="center">
  <img src="https://raw.githubusercontent.com/jztan/blueclaw/master/blueclaw-arch.svg" alt="BlueClaw Architecture" width="800">
</p>

| Module | Purpose |
|---|---|
| `cli.py` | Typer entrypoints, welcome banner, trace tooling |
| `runner.py` | Unified agent runner — single owner of agent construction, invocation, capture, and MCP cleanup (`runner_session`, `run_turn`) |
| `session.py` | Config, model + agent factory (`create_agent`), chat loop, background context updater |
| `server.py` | HTTP API gateway — `/message`, `/message/stream`, `/upload`, `/playground`, bearer auth, per-conversation locks |
| `bridges/` | Messenger bridges — platform-agnostic router + python-telegram-bot adapter; per-chat workspaces |
| `workspace.py` | Sandbox enforcement; context/history/trace I/O; multi-workspace resolver |
| `observer.py` | Structured tool tracing + output truncation |
| `context.py` | Observation masking and hybrid summarization |
| `skills.py` | Skill discovery — project + global scope resolution |
| `lessons.py` | Behavioral hints extracted from past traces, injected into the system prompt |
| `models.py` | Pydantic models, trace schema, cost calculation, error classification |
| `launcher.py` | Docker sandbox decision — env composition, argv assembly, `execvp` into `docker run` |
| `testing.py` | Test spec loading, eval orchestration, assertions, TAP/JUnit formatters |
| `tools/` | Web, shell, MCP wiring (factory pattern) |
| `approval.py` | Shell command + domain allowlist hooks |

Built on [Strands Agents SDK](https://github.com/strands-agents/sdk-python).

## Roadmap

See [docs/roadmap.md](docs/roadmap.md) for the full roadmap with milestone details.

## Contributing

```bash
pip install -e ".[dev]"
pip install pre-commit && pre-commit install   # mirrors CI lint locally
pytest
flake8 blueclaw/ tests/
black --check blueclaw/ tests/
```

Bug reports and pull requests are welcome. See [docs/contributing.md](docs/contributing.md) for the full guide.

## Links

- [AI Agent Observability Without a Dashboard](https://blog.jztan.com/ai-agent-observability-without-dashboard/) — why we built structured tracing into the terminal instead of a hosted service
- [I Cut My AI Agent's Token Costs 21% Without Changing the Model](https://blog.jztan.com/how-i-cut-ai-agent-token-costs/) — the benchmarks behind blueclaw's `ObservationMaskingManager`
- [How I Debug AI Agents Like Code (Not Guesswork)](https://blog.jztan.com/debug-ai-agents-like-code/) — a walkthrough of the 10 `trace` CLI commands
- [I Built CI for My AI Agent (It Catches What You Miss)](https://blog.jztan.com/i-built-ci-for-ai-agents/) — why behavioral contracts beat LLM-as-a-judge for agent CI

## License

[MIT](LICENSE)
