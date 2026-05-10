# Model Support

blueclaw is model-agnostic. Provider SDKs are loaded lazily, so you only need the keys and packages for the providers you actually use. The agent loop, streaming, and tool use are handled by the [Strands Agents SDK](https://github.com/strands-agents/sdk-python) — blueclaw just selects and constructs the model.

## Supported providers

| Provider key | Backend | Auth | Notes |
|---|---|---|---|
| `anthropic` | `strands.models.AnthropicModel` | `ANTHROPIC_API_KEY` | Default. Cost tracked when `model_id` is in the pricing table. |
| `openai` | `strands.models.OpenAIModel` | `OPENAI_API_KEY` | Cost not tracked (no pricing table entries). |
| `ollama` | `strands.models.OllamaModel` | none — local | Talks to `http://localhost:11434` (host=`None`). Free, no cost reported. |
| `litellm` | `strands.models.LiteLLMModel` | provider-specific (e.g. `GEMINI_API_KEY`) | Universal adapter for Gemini, Bedrock, Mistral, etc. |

## Selecting a model

Three precedence levels, highest first:

1. **CLI flag** — `--model provider/model_id` (also `-m`, except on `blueclaw serve` which only accepts `--model`)
2. **`blueclaw.yaml`** — `model.provider` + `model.model_id`
3. **Defaults** — `anthropic` / `claude-sonnet-4-6`

```bash
blueclaw                                          # uses blueclaw.yaml or default
blueclaw --model anthropic/claude-haiku-4-5-20251001
blueclaw --model openai/gpt-4.1-mini
blueclaw --model ollama/llama3
blueclaw --model litellm/gemini/gemini-2.0-flash  # everything after litellm/ goes to LiteLLM
```

The override format is strictly `provider/model_id`. Bare model names (e.g. `claude-sonnet-4-6` with no provider) raise `ValueError`.

The `--model` flag is accepted by `blueclaw`, `blueclaw run`, `blueclaw serve`, and `blueclaw test`.

## Configuration

`blueclaw.yaml` at the project root:

```yaml
model:
  provider: anthropic
  model_id: claude-sonnet-4-6
```

| Field | Default | Applies to |
|---|---|---|
| `provider` | `anthropic` | all |
| `model_id` | `claude-sonnet-4-6` | all |

`max_tokens` is fixed at `4096` in `SessionConfig` and passed to `AnthropicModel` (other providers ignore it). It is not currently configurable via `blueclaw.yaml` — change the default in `blueclaw/models.py` if you need a different cap.

## API keys

Set in `.env` at the project root. Keys are only required for the provider you select — missing keys for unused providers are fine.

```
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
GEMINI_API_KEY=...           # picked up by LiteLLM for gemini/* models
```

`anthropic` and `openai` providers fail fast with a clear error when their key is missing. `ollama` needs no key. `litellm` defers to the underlying provider's environment variables.

## Cost tracking

Cost is computed from token usage against a static pricing table in `blueclaw/models.py`:

| `model_id` | Input $/1k | Output $/1k |
|---|---|---|
| `claude-sonnet-4-6` | 0.003 | 0.015 |
| `claude-sonnet-4-20250514` | 0.003 | 0.015 |
| `claude-opus-4-6` | 0.015 | 0.075 |
| `claude-opus-4-1-20250620` | 0.015 | 0.075 |
| `claude-haiku-4-5-20251001` | 0.0008 | 0.004 |

Models not in the table report `cost: null` in traces, history, and the API response. Tokens are still counted. To track cost for a new Claude model or a non-Anthropic model, add an entry to `MODEL_PRICING`.

## Provider-specific notes

### Anthropic

```python
AnthropicModel(model_id=config.model_id, max_tokens=config.max_tokens)
```

Both `model_id` and `max_tokens` are required by the Strands constructor. Default `max_tokens=4096` is enough for typical agent turns; raise it for long-form generation.

### OpenAI

```python
OpenAIModel(model_id=config.model_id)
```

No pricing entries are pre-populated, so cost reports as `null`. Otherwise interchangeable with Anthropic for tool use and streaming.

### Ollama (local)

```python
OllamaModel(None, model_id=config.model_id)
```

`host=None` resolves to `http://localhost:11434`. Make sure the model is pulled first:

```bash
ollama pull llama3
blueclaw --model ollama/llama3
```

Tool-calling quality varies by local model — small models often fail the agent loop. Use `llama3.1:8b` or larger for usable results.

### LiteLLM

```python
LiteLLMModel(model_id=config.model_id)
```

Pass through any LiteLLM-supported `provider/model` string after the `litellm/` prefix:

```bash
blueclaw --model litellm/gemini/gemini-2.0-flash
blueclaw --model litellm/bedrock/anthropic.claude-3-5-sonnet-20241022-v2:0
```

LiteLLM reads its own environment variables (`GEMINI_API_KEY`, `AWS_*`, etc.). Refer to the [LiteLLM provider docs](https://docs.litellm.ai/docs/providers) for naming.

## Switching models mid-project

Model selection is per-invocation — there is no persistent "current model" state. The same workspace, `CONTEXT.md`, and `history.jsonl` are reused across providers, so you can run a cheap local model for routine work and switch to Claude for harder turns:

```bash
blueclaw --model ollama/llama3 run "list files in workspace"
blueclaw --model anthropic/claude-opus-4-6 run "refactor the indexer"
```

Each run records its `model_id` in the trace, so `blueclaw trace list` and `blueclaw trace diff` show which model produced which output.

## Adding a new provider

1. Add a branch in `build_model()` in `blueclaw/session.py` that imports the Strands model class lazily and returns an instance.
2. If the provider needs an API key, check for it and raise a clear error when missing (mirror the `anthropic` branch).
3. (Optional) Add pricing entries to `MODEL_PRICING` in `blueclaw/models.py` so cost is tracked.
4. (Optional) Document the `provider/model_id` format in this file.

Keep the branch small — Strands handles the agent loop, so a new provider is typically ~5 lines.
