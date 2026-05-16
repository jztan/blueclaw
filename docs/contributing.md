# Contributing Guide

Thank you for your interest in contributing to BlueClaw! This guide will help you get started.

## Ways to Contribute

- **Report bugs** — submit detailed issue reports with trace output where possible
- **Suggest features** — propose new capabilities or improvements
- **Fix issues** — submit pull requests for bug fixes
- **Add features** — implement new functionality
- **Improve docs** — enhance documentation and examples
- **Write tests** — add regression specs or unit test coverage

## Getting Started

### Prerequisites

- Python 3.10+
- Git
- An API key for at least one supported model provider (Anthropic, OpenAI, etc.) — or [Ollama](https://ollama.com) for fully local development

### Development Setup

1. **Fork and clone**

   ```bash
   git clone https://github.com/YOUR_USERNAME/blueclaw.git
   cd blueclaw
   ```

2. **Create a virtual environment**

   ```bash
   python -m venv .venv
   source .venv/bin/activate   # Windows: .venv\Scripts\activate
   ```

3. **Install in development mode**

   ```bash
   pip install -e ".[dev]"
   ```

4. **Configure environment**

   ```bash
   echo "ANTHROPIC_API_KEY=sk-ant-..." > .env
   ```

5. **Verify setup**

   ```bash
   pytest
   blueclaw --version
   ```

## Development Workflow

### 1. Create a branch

```bash
# Feature
git checkout -b feature/your-feature-name

# Bug fix
git checkout -b fix/issue-description
```

### 2. Make changes

- Follow existing code style and patterns
- Keep changes focused — one concern per PR
- Stay within the **complexity budget** (see Design Constraints below)
- Before adding anything, ask: does Strands already do this?

### 3. Write tests

```bash
# Run all tests
pytest

# Run a single file
pytest tests/test_workspace.py

# Run with output
pytest -s
```

For agent behavior tests, write a YAML spec and run it with `blueclaw test`:

```yaml
# specs/my-feature.yaml
tests:
  - goal: "summarise the file notes.txt"
    expected_tools: [read_file]
    expected_output_contains: "summary"
```

```bash
blueclaw test specs/my-feature.yaml
```

### 4. Code quality checks

```bash
# Lint
flake8 blueclaw/ tests/

# Format
black blueclaw/ tests/

# Check formatting without changes
black --check blueclaw/ tests/
```

### 5. Commit your changes

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```
type: brief description

Optional longer explanation.
```

**Types:** `feat` · `fix` · `docs` · `test` · `refactor` · `chore`

**Examples:**

```bash
git commit -m "feat: add tool_order assertion to regression testing"
git commit -m "fix: observer hook fires on tool timeout"
git commit -m "docs: add context management section to README"
```

**Important:** do not include AI attribution or "Co-Authored-By: Claude" in commit messages.

### 6. Push and open a pull request

```bash
git push origin feature/your-feature-name
```

Fill in the PR description using the template below.

## Pull Request Template

```markdown
## Description
Brief description of the change and motivation.

## Related issue
Fixes #123

## Changes made
- List of changes
- Additional context

## Testing
- [ ] Unit tests added or updated
- [ ] `pytest` passes locally
- [ ] `flake8` and `black --check` pass
- [ ] Regression spec added (for agent behavior changes)

## Checklist
- [ ] Complexity cost justified (see Design Constraints)
- [ ] No reimplementation of Strands built-ins
- [ ] Documentation updated if behaviour changed
- [ ] CHANGELOG.md updated under `[Unreleased]`
```

### Review process

1. CI runs lint, format check, and tests automatically
2. A maintainer reviews the code
3. Address feedback — push to the same branch
4. Approved and merged

## Design Constraints

These constraints keep BlueClaw understandable and maintainable. Please read them before proposing significant changes.

| Constraint | Rule |
|---|---|
| **Strands-first** | If Strands already does it, use Strands — don't reimplement |
| **MCP-first tools** | Prefer `MCPClient` over custom `@tool` functions |
| **Workspace sandbox** | All file I/O must stay inside the configured workspace root (default `~/blueclaw/workspace/`; per-chat under `~/blueclaw/chats/<id>/` when the Telegram bridge is running) |
| **Skills over features** | New integrations belong in `.claude/skills/`, not core files |
| **Simplicity budget** | Core source must stay at the one-sitting readable limit (~4,000 lines across ~13 files). New additions must justify their complexity. |

Before opening a PR for a new feature, ask:

1. Does Strands already do this?
2. Can this be a skill file instead of a core change?
3. Does the complexity cost justify the value?
4. Can a developer understand the full system in one sitting?

## Code Guidelines

### Strands patterns

```python
# Model construction
from strands.models import AnthropicModel
model = AnthropicModel(model_id="claude-sonnet-4-6", max_tokens=4096)

# Custom tool
from strands import tool

@tool
def my_tool(query: str) -> str:
    """Brief description shown to the model."""
    return do_something(query)

# Hook provider
from strands.hooks import HookProvider, HookRegistry, BeforeToolCallEvent

class MyHooks(HookProvider):
    def register_hooks(self, registry: HookRegistry):
        registry.add_callback(BeforeToolCallEvent, self._before)

    def _before(self, event: BeforeToolCallEvent):
        name = event.tool_use["name"]   # dict — not event.tool_use.name
```

### Error handling

```python
# Return structured errors — don't raise in tool functions
@tool
def read_file(path: str) -> str:
    """Read a file from the workspace."""
    try:
        return Path(path).read_text()
    except OSError as e:
        return f"Error reading file: {e}"
```

### Style

- Line length: 88 characters (Black default)
- Type hints on all public functions
- Docstrings on `@tool` functions — the model reads them
- No bare `python3` or `pytest` outside the venv

## Testing Guidelines

### Unit tests

```python
# tests/test_example.py
from blueclaw.models import TestCase
from blueclaw.testing import _check_assertions

def test_missing_tool_fails():
    case = TestCase(goal="test", expected_tools=["web_search"])
    failures = _check_assertions(
        case, tools_called=[], response_text="", step_count=0, cost=None
    )
    assert any("web_search" in f for f in failures)
```

### Regression specs (agent behavior)

```yaml
# specs/web-search.yaml
tests:
  - goal: "find the latest Python release"
    runs: 3
    threshold: 0.8
    expected_tools: [web_search]
    expected_output_contains: "Python"
    max_steps: 5
    max_cost: 0.05
```

```bash
blueclaw test specs/web-search.yaml
blueclaw test specs/web-search.yaml --format junit -o results.xml
```

### Coverage targets

- Aim for >80% on `blueclaw/` core modules
- Test both success and error paths
- Mock Strands `Agent` and external HTTP calls in unit tests

## Documentation

Update documentation when you:

- Add a new feature or CLI command
- Change existing behaviour
- Add configuration options

### Documentation files

| File | Purpose |
|---|---|
| `README.md` | Concise overview — links to detailed docs |
| `docs/tracing.md` | Trace commands reference |
| `docs/testing.md` | Regression testing reference |
| `docs/api.md` | HTTP API reference |
| `docs/roadmap.md` | Public milestone roadmap |
| `CHANGELOG.md` | Version history — add under `[Unreleased]` |

## Architecture Overview

```
Terminal input → cli.py → session.py → Strands Agent → Tools → workspace.py → observer.py → Response
HTTP request   → server.py ──────────────────────────────────────────────────────────────────────────^
```

| Module | Purpose |
|---|---|
| `cli.py` | Typer entrypoints, welcome banner, all trace subcommands |
| `session.py` | Config, model factory, agent, chat loop, background context updater |
| `server.py` | HTTP API gateway — POST /message, auth, CORS |
| `workspace.py` | Sandbox enforcement, context/history/trace I/O |
| `observer.py` | Structured tool tracing + output truncation |
| `context.py` | Observation masking and hybrid summarization |
| `lessons.py` | Behavioral hints from past traces injected into system prompt |
| `models.py` | Pydantic models, trace schema, cost calculation |
| `testing.py` | Test spec loader, runner, assertions, TAP/JUnit formatters |
| `tools/` | Web, shell, MCP wiring |
| `approval.py` | Shell command + domain allowlist hooks |

## Adding a New Tool

1. **Create the tool** in `blueclaw/tools/` or inline in `session.py` for simple cases:

   ```python
   from strands import tool

   @tool
   def my_tool(param: str) -> str:
       """
       What this tool does — shown to the model.

       Args:
           param: What the parameter means
       """
       # implementation
       return result
   ```

2. **Register it** in `session.py` where tools are assembled for the agent.

3. **Write tests** — unit test the function directly, and add a regression spec for agent-level behavior.

4. **Document it** in `docs/tracing.md` or a relevant doc if it affects tracing or configuration.

## Release Process

Releases are managed by maintainers:

1. Update version in `pyproject.toml` and `blueclaw/__init__.py`
2. Move `[Unreleased]` entries in `CHANGELOG.md` to the new version
3. Create and push a version tag: `git tag vX.Y.Z && git push origin vX.Y.Z`
4. CI publishes to PyPI automatically

## Community Guidelines

- Be respectful and constructive
- Welcome newcomers — everyone starts somewhere
- Focus feedback on the code, not the person
- Keep discussions in GitHub Issues and Pull Requests

## Getting Help

- Search [existing issues](https://github.com/jztan/blueclaw/issues) first
- Open a new issue for bugs or questions
- For design questions, open a discussion before writing code
