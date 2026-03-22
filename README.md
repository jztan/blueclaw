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
</p>

---

- **Structured traces** — every run writes a structured JSON trace, queryable from the terminal with no external service
- **Regression testing** — define expected behavior in YAML; run as CI with TAP or JUnit output and Wilson CI scoring
- **Context management** — observation masking keeps token cost low across long sessions without losing quality
- **Trace replay** — step through any recorded run interactively
- **Trace diff** — compare steps, tokens, and cost between any two runs
- **HTTP API** — `blueclaw serve` exposes the agent over HTTP with bearer auth and CORS

## Quickstart

```bash
pip install blueclaw
blueclaw init
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env
blueclaw
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
```

Bearer token auth (`BLUECLAW_API_KEY`), 1 MB body cap, 300 s timeout, CORS for localhost. Every API request writes a trace visible in `blueclaw trace ui`.

## Model Support

```bash
blueclaw                                    # Anthropic (default)
blueclaw --model ollama/llama3              # Ollama (local)
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
  - mcp:https://localhost:8080/sse

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
| `server.py` | HTTP API gateway (`blueclaw serve`) — POST /message, auth, CORS |
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

## License

[MIT](LICENSE)
