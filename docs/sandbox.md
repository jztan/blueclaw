# Docker Sandbox (v2.5+)

Opt-in container isolation for the entire blueclaw agent process. Off by default.

## When to turn it on

- You run skills you didn't author and want a real boundary against `rm -rf $HOME`.
- You want reproducible runs — the image digest is recorded in every trace.
- You're preparing for v3's network-level egress allowlist.

## When **not** to turn it on

- macOS bind-mount performance is noticeable for write-heavy workloads.
- `blueclaw test` runs each case in a fresh container; ~1–2s cold-start per case.

## Quick start

```bash
# 1. Build the runtime image (once per blueclaw release or per dev branch SHA).
blueclaw sandbox build

# 2. Diagnose:
blueclaw sandbox doctor

# 3. Enable docker mode in blueclaw.yaml:
cat >> blueclaw.yaml <<EOF
sandbox:
  mode: docker
EOF

# 4. Run as usual:
blueclaw run "what time is it"
```

If `docker` is missing or the image hasn't been built, `blueclaw run` will print the
exact command to fix it and exit non-zero.

## Configuration reference

```yaml
sandbox:
  mode: docker                 # "inprocess" (default) | "docker"
  image: null                  # null ⇒ auto-resolved tag; override with "your/img:tag"
  network: bridge              # "bridge" (default) | "none" | "proxy" (reserved)
  cpu: 1.0                     # --cpus
  memory_mb: 1024              # --memory
  pids: 512                    # --pids-limit
  on_unavailable: error        # "error" (default) | "fallback"
  user: host                   # "host" (default, $(id -u):$(id -g)) | "1000:1000"
  env_files: null              # null ⇒ default list (see below); [] disables loading
  extra_mounts: []             # list of {host, container, mode: ro|rw}
  extra_env: {}                # KEY: VALUE, or KEY: "@host" to pass through host env
```

## Environment variables

Env vars are composed deterministically from these layers (low → high precedence):

1. **Built-in allowlist** read from the launcher's shell: `ANTHROPIC_API_KEY`,
   `OPENAI_API_KEY`, `GOOGLE_API_KEY`, `GEMINI_API_KEY`, `OLLAMA_HOST`,
   `BLUECLAW_API_KEY`, `HTTPS_PROXY`, `HTTP_PROXY`, `NO_PROXY`, `GH_TOKEN`,
   `GITHUB_TOKEN`. Missing vars are silently skipped.
2. **`~/blueclaw/.env`** — user-global dotenv.
3. **`<project>/.env`** — project-local dotenv (the same file `python-dotenv` already
   loads on the host at startup, so the same keys reach the container).
4. **`<project>/.env.docker`** — docker-specific overrides (sits next to `blueclaw.yaml`).
5. **`extra_env`** in `blueclaw.yaml` — highest precedence. Use `KEY: "@host"` to
   forward an arbitrary host env var by name.

Override the default list (2)+(3)+(4) via `sandbox.env_files: [path1, path2, ...]`.
Set `env_files: []` to disable file-based env entirely.

### Dotenv file format

```
# Comments start at column 0 with '#'. Lines like `KEY=VALUE`.
ANTHROPIC_API_KEY=sk-...
HTTPS_PROXY=http://corp.proxy:3128
SOME_FLAG="value with spaces"
LITERAL='$NOT_EXPANDED'
```

No shell expansion, no `$VAR` substitution. Identical interpretation in any shell.

**`.env.docker` is a secret file** — `blueclaw init` adds it to `.gitignore`. Don't
commit it.

## Image tagging

| Install style | Tag |
|---|---|
| `pip install blueclaw` | `blueclaw/runtime:<package-version>` |
| `pip install -e .` (editable) | `blueclaw/runtime:dev-<short-sha>` |

`sandbox build` picks the right tag automatically. There's deliberately no `:latest`.

**On reproducibility:** the bundled `docker/Dockerfile` doesn't pin upstream APT or PyPI versions, so two builds on different days may produce binary-different images even from the same blueclaw commit. The trace metadata records the image digest at run time, so any single run is reproducible against *that* specific build — but cross-host or cross-day digest comparisons aren't meaningful. If you need stricter reproducibility (e.g., for compliance), pin the base image by digest in your own Dockerfile and set `sandbox.image` to your tag.

## Troubleshooting

### "Docker unavailable"

If you've installed Docker but `blueclaw sandbox doctor` still reports it
unavailable, either Docker isn't running or your user isn't in the docker group.

To degrade gracefully on machines without Docker:

```yaml
sandbox:
  mode: docker
  on_unavailable: fallback   # falls back to in-process with a stderr warning
```

### "image '...' not found"

```bash
blueclaw sandbox build
```

### Files written by the agent are owned by root or uid 1000

You're probably overriding `sandbox.user`. Set it back to `host`:

```yaml
sandbox:
  user: host
```

### `gh` (GitHub CLI) not available in the container

The default runtime image does not include `gh` — it isn't packaged in standard
Debian repos and adding an apt source bloats the image. If a skill needs `gh`,
either add it via `extra_mounts` (mount the host binary) or build a custom
image extending `blueclaw/runtime`:

```dockerfile
FROM blueclaw/runtime:<tag>
USER root
RUN apt-get update && apt-get install -y --no-install-recommends gnupg \
    && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        | gpg --dearmor -o /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update && apt-get install -y --no-install-recommends gh \
    && rm -rf /var/lib/apt/lists/*
USER blueclaw
```

### macOS performance

Bind mounts on Docker Desktop for Mac are slower than native — expect 5–10× for
write-heavy workloads. The in-process sandbox is faster for tight dev loops.
