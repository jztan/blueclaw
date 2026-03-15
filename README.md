<p align="center">
  <img src="blueclaw-logo.PNG" alt="BlueClaw" width="400">
</p>

<p align="center">
  <strong>BlueClaw treats AI agents like debuggable programs, not black boxes.</strong><br>
  Built on <a href="https://github.com/strands-agents/sdk-python">Strands Agents SDK</a>
</p>

<p align="center">
  <a href="#quickstart">Quickstart</a> •
  <a href="#tracing--observability">Tracing</a> •
  <a href="#features">Features</a> •
  <a href="#model-support">Models</a> •
  <a href="#configuration">Configuration</a> •
  <a href="#architecture">Architecture</a>
</p>

---

## What is BlueClaw?

BlueClaw is a terminal-based AI agent with built-in execution tracing, enabling developers to inspect, replay, and debug agent behavior step by step.

Most AI agents are black boxes — when something goes wrong, you don't know if it was the model reasoning, the tool input, the tool output, or a bad retry. BlueClaw records every tool call with timing, inputs, and outputs, then gives you CLI tools to understand what happened.

```
blueclaw> research the MCP ecosystem, focus on Python SDKs
● web_search({"query": "MCP Model Context Protocol Python SDK"})
  ✓ 1.2s
● http_request({"url": "https://modelcontextprotocol.io/..."})
  ✓ 0.8s
Done · 2 steps · 1840 tokens · $0.0073 · 4.1s
```

## Quickstart

```bash
# Install
pip install -e .

# Initialize workspace
blueclaw init

# Set your API key
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env

# Start an interactive session
blueclaw

# Or run a single prompt
blueclaw run "summarize the latest Python 3.13 release notes"
```

## Tracing & Observability

Every agent run is recorded as a structured JSON trace with per-step timing, tool inputs, outputs, and errors.

```
blueclaw trace graph 20260315-054426

search for Python 3.13 new features
├── web_search (1ms) ✓  query: Python 3.13 new features
├── web_search (1ms) ✓  query: Python 3.13 new features list 2024
└── http_request (366ms) ✓  url: https://docs.python.org/3.13/whatsnew/3.13.html
```

- **`trace show`** — detailed step table with timing and status
- **`trace graph`** — tree view of tool call sequences
- **`trace explain`** — LLM-powered post-hoc explanation of what happened and why
- **`trace diff`** — compare two runs side by side (steps, tokens, cost, duration deltas)
- **`trace replay`** — interactive step-through debugger
- **`trace timeline`** — waterfall chart with per-step start offset, duration, cumulative timing, and overhead breakdown
- **`trace stats`** — aggregate metrics across runs: avg tokens/cost, timing percentiles, top tools, failure classification (`--since N`, `--model`)

## Features

- **Execution tracing** — structured JSON traces with full observability tooling (see above)
- **Model-agnostic** — swap between Claude, Ollama, OpenAI, Gemini with one flag
- **Persistent memory** — `CONTEXT.md` updates in the background after each turn (instant exit), `history.jsonl` logs every run
- **Interactive + scripted modes** — `blueclaw` for chat, `blueclaw run "..."` for one-shot
- **Shell execution** — sandboxed `shell_command` tool with deny-list, 30s timeout, and interactive approval
- **Workspace sandbox** — path validation + destructive command deny-list
- **Approval hooks** — interactive confirmation for shell commands and new web domains
- **Crash recovery** — per-turn checkpoints in `.blueclaw/last_turn.md`
- **Output truncation** — 12k char limit prevents context blowout
- **MCP support** — bundled `pdf-mcp` server, custom stdio/SSE servers via config
- **Skill system** — progressive loading, index in prompt, full content on demand

## Model Support

```bash
# Anthropic (default)
blueclaw

# Ollama (local, no data leaves your machine)
blueclaw --model ollama/llama3

# OpenAI
blueclaw --model openai/gpt-4.1-mini

# Gemini via LiteLLM
blueclaw --model litellm/gemini/gemini-2.0-flash
```

Set API keys in `.env`:

```
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
```

## Commands

| Command | Description |
|---|---|
| `blueclaw` | Start interactive session |
| `blueclaw run "..."` | Execute a single prompt and exit |
| `blueclaw init` | Initialize workspace directory |
| `blueclaw history` | View past run history |
| `blueclaw trace list` | List recent execution traces |
| `blueclaw trace show <run_id>` | Show detailed trace for a run |
| `blueclaw trace explain <run_id>` | LLM-powered explanation of a recorded trace |
| `blueclaw trace graph <run_id>` | Tree view of tool call sequence |
| `blueclaw trace diff <id1> <id2>` | Compare two traces side by side |
| `blueclaw trace replay <run_id>` | Step through a trace interactively |
| `blueclaw trace timeline <run_id>` | Waterfall timeline with timing and overhead |
| `blueclaw trace stats` | Aggregate metrics across all traces |
| `blueclaw --version` | Print version |
| `blueclaw --model provider/model` | Override model for this session |

## Configuration

`blueclaw.yaml` in your project root:

```yaml
model:
  provider: anthropic
  model_id: claude-sonnet-4-6

workspace:
  path: ~/blueclaw/workspace/

tools:
  - web
  - shell                              # sandboxed shell execution (enables gh, git, etc.)
  - pdf
  - mcp:https://localhost:8080/sse     # custom MCP server

allowlist_domains:
  - github.com
  - docs.python.org
```

## Architecture

```
Terminal input → cli.py → session.py → Strands Agent → Tools → workspace.py (sandbox) → observer.py (trace) → Response
```

| Module | Purpose | Lines |
|---|---|---|
| `cli.py` | Typer entrypoints, welcome banner, trace tooling | ~714 |
| `session.py` | Config, model factory, agent, chat loop, background context updater | ~537 |
| `workspace.py` | Sandbox enforcement, context/history/trace I/O | ~201 |
| `observer.py` | Structured tool tracing + output truncation | ~151 |
| `models.py` | Pydantic models, trace schema, cost calculation, error classification | ~124 |
| `tools/` | Web, shell, MCP wiring (factory pattern) | ~155 |
| `approval.py` | Shell command + domain allowlist hooks | ~51 |

## Workspace Structure

```
~/blueclaw/workspace/
├── CONTEXT.md                    # Persistent agent knowledge (human-editable)
└── .blueclaw/
    ├── history.jsonl             # Append-only run log
    ├── last_turn.md              # Crash recovery checkpoint
    └── traces/                   # Structured execution traces
        └── 20260315-101201.json  # One JSON file per run
```

## Development

```bash
# Install in dev mode
pip install -e ".[dev]"

# Run tests
pytest

# Lint
flake8 blueclaw/ tests/
black --check blueclaw/ tests/
```

## License

MIT
