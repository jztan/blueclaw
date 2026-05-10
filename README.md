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

- **Structured traces** — every run writes a structured JSON trace, queryable from the terminal with no external service
- **Regression testing** — define expected behavior in YAML; run as CI with TAP or JUnit output and Wilson CI scoring
- **Context management** — observation masking keeps token cost low across long sessions without losing quality
- **Trace replay & diff** — step through any recorded run interactively, or compare steps, tokens, and cost between two runs
- **HTTP API + stateful conversations** — `blueclaw serve` exposes the agent over HTTP with bearer auth, SSE streaming, a concurrency cap, per-`conversation_id` history persisted via `FileSessionManager`, plus `POST /upload` for attaching files (PDF, text, images, csv, json, zip) to a conversation
- **File attachments with native vision** — drop `@<path>` (or just paste a bare/quoted absolute path) into any CLI prompt; PNG/JPEG/GIF/WEBP attachments reach vision-capable models as Strands `image` blocks, while PDFs and text reuse the shell/pdf-mcp tools. Works the same way over HTTP via `POST /upload` + `file_ids`
- **Built-in playground** — `GET /playground` ships a single-page chat UI with `blueclaw serve` for manual stateful + streaming testing, including paperclip + drag-drop file attachments

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

Every run produces a structured JSON trace. Ten CLI commands let you inspect, compare, and replay runs without a hosted dashboard.

```
$ blueclaw trace graph 20260315-054426

search for Python 3.13 new features
├── web_search (1ms) ✓  query: Python 3.13 new features
├── web_search (1ms) ✓  query: Python 3.13 new features list 2024
└── http_request (366ms) ✓  url: https://docs.python.org/3.13/whatsnew/3.13.html
```

`trace list` · `trace show` · `trace graph` · `trace timeline` · `trace diff` · `trace explain` · `trace replay` · `trace stats` · `trace ui` · `trace purge`

### Regression Testing — [docs/testing.md](docs/testing.md)

Define expected behavior in YAML, run as a CI test suite with TAP or JUnit output. Multi-run Wilson CI scoring handles non-determinism.

```bash
blueclaw test spec.yaml
blueclaw test spec.yaml --format junit -o results.xml
```

11 deterministic assertions: tools called, output content, file existence, cost, step count, duration, tool order.

### Context Management

Tool outputs from older turns are automatically masked to keep token cost low across long sessions without losing model reasoning quality. A hybrid summarization mode is available for very long conversations.

### HTTP API — [docs/api.md](docs/api.md)

Expose the agent over HTTP for programmatic access or tool integration.

```bash
blueclaw serve                          # http://127.0.0.1:8420
curl -X POST http://127.0.0.1:8420/message \
  -d '{"message": "what is in the workspace?"}' | jq .

# Stream tokens as they're generated:
curl -N -X POST http://127.0.0.1:8420/message/stream \
  -d '{"message": "what is in the workspace?"}'

# Attach a file, then reference its file_id in /message:
FID=$(curl -s -X POST http://127.0.0.1:8420/upload \
  -F "file=@photo.jpg" -F "conversation_id=c-1" | jq -r .file_id)
curl -X POST http://127.0.0.1:8420/message \
  -d "{\"message\":\"describe this\",\"conversation_id\":\"c-1\",\"file_ids\":[\"$FID\"]}"
```

Bearer token auth (`BLUECLAW_API_KEY`), 1 MB body cap on JSON, 25 MB on `/upload`, 300 s timeout, CORS for localhost. A shared `asyncio.Semaphore` (default 4, configurable via `--max-concurrent`) caps simultaneous agent runs. Every API request writes a trace visible in `blueclaw trace ui`.

## Model Support — [docs/models.md](docs/models.md)

```bash
blueclaw                                    # Anthropic (default)
blueclaw --model ollama/llama3.1:8b         # Ollama (local)
blueclaw --model openai/gpt-4.1-mini       # OpenAI
blueclaw --model litellm/gemini/gemini-2.0-flash  # Gemini via LiteLLM
```

Set API keys in `.env`:

```
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
```

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

## Architecture

<p align="center">
  <img src="https://raw.githubusercontent.com/jztan/blueclaw/master/blueclaw-arch.svg" alt="BlueClaw Architecture" width="800">
</p>

| Module | Purpose |
|---|---|
| `cli.py` | Typer entrypoints, welcome banner, trace tooling |
| `session.py` | Config, model factory, agent, chat loop, background context updater |
| `server.py` | HTTP API gateway (`blueclaw serve`) — `/message`, `/message/stream`, `/playground`, `/health`, `/api/traces`; bearer auth, CORS, per-conversation locks |
| `workspace.py` | Sandbox enforcement, context/history/trace I/O |
| `observer.py` | Structured tool tracing + output truncation |
| `context.py` | Observation masking and hybrid summarization for context management |
| `lessons.py` | Extracts behavioral hints from past traces and injects into system prompt |
| `models.py` | Pydantic models, trace schema, cost calculation, error classification |
| `testing.py` | Test spec loading, runner, assertions, formatters, stub replay |
| `tools/` | Web, shell, MCP wiring (factory pattern) |
| `approval.py` | Shell command + domain allowlist hooks |

Built on [Strands Agents SDK](https://github.com/strands-agents/sdk-python).

## Roadmap

See [docs/roadmap.md](docs/roadmap.md) for the full roadmap with milestone details.

## Contributing

```bash
pip install -e ".[dev]"
pytest
flake8 blueclaw/ tests/
black --check blueclaw/ tests/
```

Bug reports and pull requests are welcome. See [docs/contributing.md](docs/contributing.md) for the full guide.

## Links

- [AI Agent Observability Without a Dashboard](https://blog.jztan.com/ai-agent-observability-without-dashboard/) — The story behind blueclaw's design: why we built structured tracing into the terminal instead of a hosted service
- [I Cut My AI Agent's Token Costs 21% Without Changing the Model](https://blog.jztan.com/how-i-cut-ai-agent-token-costs/) — Benchmarks behind blueclaw's `ObservationMaskingManager`: why replacing stale tool outputs with placeholders beats LLM summarization on cost and speed
- [How I Debug AI Agents Like Code (Not Guesswork)](https://blog.jztan.com/debug-ai-agents-like-code/) — A walkthrough of blueclaw's 10 `trace` CLI commands: `trace list` → `show` → `timeline` → `diff` turns "re-run and guess" debugging into actual inspection in under a minute

## License

[MIT](LICENSE)
