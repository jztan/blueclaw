# Regression Testing

Define expected agent behavior in YAML, run it as a test suite, get CI-friendly output.

## Test spec

```yaml
# test-spec.yaml
tests:
  - goal: search for Python web frameworks and save to frameworks.txt
    expected_tools: [web_search, shell_command]
    expected_file_contains:
      frameworks.txt: "Django"
    tool_order: [web_search, shell_command]
    forbidden_tools: [http_request]
    max_steps: 5

  - goal: check the current weather in Tokyo using wttr.in
    expected_tools: [http_request]
    expected_output_contains: Tokyo
    max_cost: 0.05
    runs: 5
    threshold: 0.55

model: anthropic/claude-haiku-4-5-20251001
allowlist_domains:
  - wttr.in
```

## Run tests

```bash
$ blueclaw test test-spec.yaml

TAP version 13
1..2
ok 1 - search for Python web frameworks and save to frameworks.txt
ok 2 - check the current weather in Tokyo using wttr.in
```

## Assertions

| Field | Check |
|---|---|
| `expected_tools` | Every listed tool was called (subset match) |
| `expected_output_contains` | Case-insensitive substring match on response |
| `max_steps` | Agent used no more than N tool calls |
| `max_cost` | Run cost stayed under budget |
| `forbidden_tools` | None of these tools were called |
| `expected_files` | Each path exists in workspace after the run |
| `expected_file_contains` | File exists AND contains substring (case-insensitive) |
| `forbidden_output_contains` | Substring must NOT appear in response |
| `output_regex` | Regex pattern must match response |
| `tool_order` | Tools appear in this subsequence order |
| `max_duration_s` | Wall-clock time under budget |

## Spec-level fields

| Field | Purpose |
|---|---|
| `model` | Override model for all tests in the spec |
| `allowlist_domains` | Domains allowed for `http_request` (merged with `blueclaw.yaml`) |

## Multi-run with Wilson CI

LLMs are non-deterministic. Set `runs: N` (N > 1) to execute multiple times and get a statistically valid verdict instead of brittle pass/fail:

- **Pass** — Wilson CI lower bound >= threshold
- **Fail** — Wilson CI upper bound < threshold
- **Inconclusive** — CI straddles the threshold (needs more runs)

Inconclusive tests exit 0 so they don't break CI, but surface as `# INCONCLUSIVE` in TAP and `<skipped>` in JUnit XML.

## Output formats

```bash
blueclaw test spec.yaml                          # TAP to stdout (default)
blueclaw test spec.yaml --format junit           # JUnit XML to stdout
blueclaw test spec.yaml -o results.xml -f junit  # write to file
blueclaw test spec.yaml --dry-run                # validate spec, no API calls
blueclaw test spec.yaml --keep-workspace         # preserve workspaces for inspection
blueclaw test spec.yaml --model anthropic/claude-haiku-4-5-20251001
```

Exit code: `0` on all pass/inconclusive, `1` on any failure.

## Per-run diagnostics

With `--keep-workspace`, each run directory contains `.blueclaw/result.json`:

```bash
$ cat /tmp/blueclaw-test-.../case-007/run-002/.blueclaw/result.json
{
  "goal": "check the current weather in Tokyo using wttr.in",
  "passed": true,
  "verdict": "pass",
  "tools_called": ["http_request"],
  "cost": 0.009,
  "duration_s": 4.4
}
```

## Stub replay

Re-run a recorded trace with stubbed tool outputs — no real execution, no API cost for tools:

```bash
$ blueclaw trace replay 20260315-054426 --stub-tools

Original: web_search -> http_request
Replayed: web_search -> http_request
Result: MATCH (same tool sequence)
```

Use `--model` to test whether a different model makes the same tool choices given the same context.
