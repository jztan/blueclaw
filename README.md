<p align="center">
  <img src="https://raw.githubusercontent.com/jztan/blueclaw/master/blueclaw-logo.PNG" alt="BlueClaw" width="400">
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

<p align="center">
  <a href="https://pypi.org/project/blueclaw/"><img src="https://img.shields.io/pypi/v/blueclaw.svg" alt="PyPI Version"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/jztan/blueclaw.svg" alt="License"></a>
  <a href="https://pypi.org/project/blueclaw/"><img src="https://img.shields.io/pypi/pyversions/blueclaw.svg" alt="Python Version"></a>
  <a href="https://github.com/jztan/blueclaw/issues"><img src="https://img.shields.io/github/issues/jztan/blueclaw.svg" alt="GitHub Issues"></a>
  <a href="https://github.com/jztan/blueclaw/actions/workflows/ci.yml"><img src="https://github.com/jztan/blueclaw/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
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

Every agent run is recorded as a structured JSON trace with per-step timing, tool inputs, outputs, and errors. Eight CLI commands let you inspect runs after the fact — no dashboards, no external services, no setup.

### See what happened: `trace graph`

Quick view of the tool call sequence for any run.

```
$ blueclaw trace graph 20260315-054426

search for Python 3.13 new features
├── web_search (1ms) ✓  query: Python 3.13 new features
├── web_search (1ms) ✓  query: Python 3.13 new features list 2024
└── http_request (366ms) ✓  url: https://docs.python.org/3.13/whatsnew/3.13.html
```

### Find the bottleneck: `trace timeline`

See where time actually goes — tool execution vs. model reasoning overhead.

```
$ blueclaw trace timeline 20260315-054426

Goal: search for Python 3.13 new features
Model: claude-sonnet-4-6 · 3 steps · 1840 tokens · $0.0073

 #    Tool             Start     Duration  Cumulative  Bar
 1    web_search         +0ms       1ms         1ms    █
 2    web_search       +120ms       1ms         2ms    █
 3    http_request     +250ms     366ms       368ms    ████████████████████████████████████████

Tool time: 368ms · Wall time: 4100ms · Overhead: 3732ms (91%)
```

### Understand why: `trace explain`

Feed a recorded trace to an LLM for post-hoc explanation. Useful when the agent took an unexpected path.

```
$ blueclaw trace explain 20260315-054426

The agent searched for Python 3.13 features, found the results too generic,
refined its query to include "list 2024", then fetched the official changelog
from docs.python.org. The two-step search pattern suggests the first results
didn't contain enough detail...

Post-hoc explanation · not the agent's actual reasoning
```

### Compare two runs: `trace diff`

Did your prompt change make things better or worse?

```
$ blueclaw trace diff 20260315-054426 20260315-071830

Run A: 20260315-054426  Run B: 20260315-071830
Goal A: search for Python 3.13 new features
Goal B: search for Python 3.13 new features

Steps:  3 → 2 (-1)
Tokens: 1840 → 1200 (-640)
Cost:   $0.0073 → $0.0048
Time:   368ms → 420ms (+52ms)
```

### Debug step by step: `trace replay`

Interactive step-through — see inputs and outputs for each tool call.

```
$ blueclaw trace replay 20260315-054426

Step 1: web_search (1ms) ✓
  input query: Python 3.13 new features
  output: Found 10 results...
[Enter] next · [q] quit >
```

### Track performance over time: `trace stats`

Aggregate metrics across all your runs. Answer "how is my agent performing?" at a glance.

```
$ blueclaw trace stats --since 7

Trace Stats · 23 runs · last 7 days

Overview
  Total runs:     23
  Total steps:    87
  Avg steps/run:  3.8
  Avg tokens/run: 2,450
  Avg cost/run:   $0.0082
  Total cost:     $0.19

Timing
  Avg duration:    5.1s
  Median duration: 4.2s
  p95 duration:    12.3s
  Avg tool time:   2.1s (41% of wall)

Top Tools (by frequency)
  shell_command        34 calls (39%)
  web_search           28 calls (32%)
  http_request         18 calls (21%)
  file_read             7 calls (8%)

Failed Steps (3 across 2 runs · 3.4% step failure rate)
  timeout              2 (67%)
  network              1 (33%)
```

Filter by model to compare providers:

```
$ blueclaw trace stats --model ollama/llama3
$ blueclaw trace stats --model claude-sonnet-4-6 --since 30
```

### All trace commands

| Command | Use case |
|---|---|
| `trace list` | Find a run ID to inspect |
| `trace show <id>` | Detailed step table with timing |
| `trace graph <id>` | Quick tree view of tool sequence |
| `trace timeline <id>` | Find bottlenecks — where does time go? |
| `trace explain <id>` | LLM explains what happened and why |
| `trace diff <id1> <id2>` | Compare two runs (A/B test prompts) |
| `trace replay <id>` | Step-through debugger for tool calls |
| `trace stats` | Aggregate performance across all runs |
| `trace purge` | Delete old traces (default: 30 days) |

## Features

- **Execution tracing** — structured JSON traces with full observability tooling (see above)
- **Model-agnostic** — swap between Claude, Ollama, OpenAI, Gemini with one flag
- **Web search** — DuckDuckGo search via `ddgs`, returns top 5 results with titles, URLs, and snippets
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
| `blueclaw trace purge` | Delete traces older than retention period |
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
  trace_retention_days: 30             # auto-purge old traces; 0 = keep forever

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

<p align="center">
  <img src="https://raw.githubusercontent.com/jztan/blueclaw/master/blueclaw-arch.svg" alt="BlueClaw Architecture" width="800">
</p>

| Module | Purpose |
|---|---|
| `cli.py` | Typer entrypoints, welcome banner, trace tooling |
| `session.py` | Config, model factory, agent, chat loop, background context updater |
| `workspace.py` | Sandbox enforcement, context/history/trace I/O |
| `observer.py` | Structured tool tracing + output truncation |
| `models.py` | Pydantic models, trace schema, cost calculation, error classification |
| `tools/` | Web, shell, MCP wiring (factory pattern) |
| `approval.py` | Shell command + domain allowlist hooks |

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
