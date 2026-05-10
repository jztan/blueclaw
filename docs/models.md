# Model Support

blueclaw is model-agnostic. Provider SDKs are loaded lazily, so you only need the keys and packages for the providers you actually use. The agent loop, streaming, and tool use are handled by the [Strands Agents SDK](https://github.com/strands-agents/sdk-python) — blueclaw just selects and constructs the model.

## Supported providers

| Provider key | Backend | Auth | Cost tracked? |
|---|---|---|---|
| `anthropic` | `strands.models.AnthropicModel` | `ANTHROPIC_API_KEY` | Yes (Claude models in pricing table) |
| `openai` | `strands.models.OpenAIModel` | `OPENAI_API_KEY` | No |
| `ollama` | `strands.models.OllamaModel` | none — local | N/A (free) |
| `litellm` | `strands.models.LiteLLMModel` | provider-specific | No |

## Quick start

```bash
blueclaw                                          # uses blueclaw.yaml or default (Anthropic)
blueclaw --model anthropic/claude-haiku-4-5-20251001
blueclaw --model openai/gpt-4.1-mini
blueclaw --model ollama/llama3.1:8b
blueclaw --model litellm/gemini/gemini-2.0-flash  # everything after `litellm/` goes to LiteLLM
```

The `--model` flag (`-m` short form, except on `blueclaw serve`) is accepted by `blueclaw`, `blueclaw run`, `blueclaw serve`, and `blueclaw test`. Format is strictly `provider/model_id` — bare names raise `ValueError`.

## Configuration

Three precedence levels, highest first:

1. **CLI flag** — `--model provider/model_id`
2. **`blueclaw.yaml`** — `model.provider` + `model.model_id`
3. **Defaults** — `anthropic` / `claude-sonnet-4-6`

`blueclaw.yaml` schema (model section):

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

API keys live in `.env` at the project root. Keys are only required for the provider you select; missing keys for unused providers are fine. `anthropic` and `openai` fail fast with a clear error when their key is missing.

---

## Provider setup

Each subsection below shows the constructor, the `blueclaw.yaml` snippet, and the `.env` keys for that provider.

### Anthropic

```python
AnthropicModel(model_id=config.model_id, max_tokens=config.max_tokens)
```

Both `model_id` and `max_tokens` are required by the Strands constructor. Default `max_tokens=4096` is enough for typical agent turns; raise it in `models.py` for long-form generation.

`blueclaw.yaml`:

```yaml
model:
  provider: anthropic
  model_id: claude-sonnet-4-6        # or claude-haiku-4-5-20251001 / claude-opus-4-6
```

`.env`:

```
ANTHROPIC_API_KEY=sk-ant-...
```

### OpenAI

```python
OpenAIModel(model_id=config.model_id)
```

No pricing entries are pre-populated, so cost reports as `null`. Otherwise interchangeable with Anthropic for tool use and streaming. The `model_id` is forwarded verbatim to `OpenAIModel`, so any name in the OpenAI catalog works.

`blueclaw.yaml`:

```yaml
model:
  provider: openai
  model_id: gpt-4.1-mini             # or gpt-4o, gpt-4o-mini, o1-mini
```

`.env`:

```
OPENAI_API_KEY=sk-...
```

### Ollama (local)

```python
OllamaModel(None, model_id=config.model_id)
```

`host=None` resolves to `http://localhost:11434`. Pull the model first and make sure `ollama serve` is running:

```bash
ollama pull llama3.1:8b
blueclaw --model ollama/llama3.1:8b
```

No `.env` needed.

**Basic `blueclaw.yaml`:**

```yaml
model:
  provider: ollama
  model_id: llama3.1:8b              # must already be pulled with `ollama pull`
```

**Tuned for tool-calling:**

```yaml
model:
  provider: ollama
  model_id: qwen2.5:14b              # better tool-calling than llama3.1:8b

tools:
  - shell
  - web

context:
  strategy: mask                     # local models benefit most from masking
  mask_after: 6                      # tighter window — local models have shorter effective context
```

Tool-calling quality varies a lot by local model. The list below reflects community reports — not benchmarked inside blueclaw. Smoke-test before relying on any row:

| Model | Notes |
|---|---|
| `qwen2.5:14b` / `qwen2.5:7b` | Strong tool-calling, recommended starting point |
| `llama3.1:8b` | Works, occasionally drops tool calls |
| `llama3.3:70b` | Best quality if you have the VRAM |
| `mistral-nemo:12b` | Decent fallback |
| `llama3.2:1b/3b`, `phi3:mini`, `gemma:2b` | Avoid — too small to drive the agent loop reliably |

### LiteLLM (gateway)

```python
LiteLLMModel(model_id=config.model_id)
```

Universal adapter — pass through any LiteLLM-supported `provider/model` string after the `litellm/` prefix. LiteLLM reads its own environment variables. Refer to the [LiteLLM provider docs](https://docs.litellm.ai/docs/providers) for naming.

**Gemini:**

```yaml
model:
  provider: litellm
  model_id: gemini/gemini-2.0-flash  # everything after `litellm/` goes here
```

```
GEMINI_API_KEY=...
```

**Bedrock Claude:**

```yaml
model:
  provider: litellm
  model_id: bedrock/anthropic.claude-3-5-sonnet-20241022-v2:0
```

```
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_REGION=us-east-1
```

---

## Cost tracking

Cost is computed from token usage against a static pricing table in `blueclaw/models.py`. Rates are stored per **1M tokens** to match Anthropic's pricing page directly. Last reviewed: see `PRICING_UPDATED` in `blueclaw/models.py` — bump it whenever you edit the table, and re-check provider list prices if it's older than ~6 months.

| `model_id` | Input $/1M | Output $/1M |
|---|---|---|
| `claude-sonnet-4-6` | 3.00 | 15.00 |
| `claude-sonnet-4-20250514` | 3.00 | 15.00 |
| `claude-opus-4-6` | 15.00 | 75.00 |
| `claude-opus-4-1-20250620` | 15.00 | 75.00 |
| `claude-haiku-4-5-20251001` | 0.80 | 4.00 |

**Prompt caching.** When Strands surfaces `cacheReadInputTokens` / `cacheWriteInputTokens` (Anthropic prompt caching), those tokens are billed at `0.1×` (read) and `1.25×` (5-minute TTL write) of the base input rate, matching Anthropic's published multipliers. Cached tokens are reported *separately* from `inputTokens`, so they aren't double-counted.

Models not in the table report `cost: null` in traces, history, and the API response. Tokens are still counted. To track cost for a new Claude model or a non-Anthropic model, add an entry to `MODEL_PRICING_PER_M` and bump `PRICING_UPDATED`.

## Switching models mid-project

Model selection is per-invocation — there is no persistent "current model" state. The same workspace, `CONTEXT.md`, and `history.jsonl` are reused across providers, so you can run a cheap local model for routine work and switch to Claude for harder turns:

```bash
blueclaw --model ollama/llama3.1:8b run "list files in workspace"
blueclaw --model anthropic/claude-opus-4-6 run "refactor the indexer"
```

Each run records its `model_id` in the trace, so `blueclaw trace list` and `blueclaw trace diff` show which model produced which output.

## Adding a new provider

1. Add a branch in `build_model()` in `blueclaw/session.py` that imports the Strands model class lazily and returns an instance.
2. If the provider needs an API key, check for it and raise a clear error when missing (mirror the `anthropic` branch).
3. (Optional) Add pricing entries to `MODEL_PRICING` in `blueclaw/models.py` so cost is tracked.
4. (Optional) Document the `provider/model_id` format in this file.

Keep the branch small — Strands handles the agent loop, so a new provider is typically ~5 lines.
