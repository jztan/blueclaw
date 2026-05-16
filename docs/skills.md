# Skills

Skills are directories that bundle agent instructions and metadata into a portable unit. blueclaw discovers installed skills at session start, advertises them to the model in the system prompt, and lets the agent load full instructions on demand via the built-in `skills` tool.

The runtime is the [Strands `AgentSkills` plugin](https://github.com/strands-agents/sdk-python) (1.30+), which follows the [AgentSkills.io](https://agentskills.io) standard. Skills written for blueclaw work in any AgentSkills-compatible runtime and vice versa.

## Quick start

```bash
mkdir -p ~/my-skill
cat > ~/my-skill/SKILL.md <<'EOF'
---
name: my-skill
description: A one-line hook the model sees up front.
---

# Body — only loaded when the agent activates this skill

Detailed instructions go here.
EOF

blueclaw skill install ~/my-skill
blueclaw skill list
blueclaw
> can you use my-skill to do X?
```

## SKILL.md format

YAML frontmatter (`---` delimited) plus markdown body. Two fields are required:

| Field | Required | Notes |
|---|---|---|
| `name` | yes | `^[a-z0-9]([a-z0-9-]*[a-z0-9])?$`, ≤64 chars, must match the parent directory name |
| `description` | yes | One-line hook injected into the system prompt; everything else stays out until the agent activates the skill |
| `version` | no | Free-form (semver recommended) |
| `license` | no | SPDX identifier, e.g. `Apache-2.0` |
| `compatibility` | no | Free-form runtime constraint, e.g. `blueclaw>=2.4` |
| `metadata` | no | Free-form key/value map; passed through verbatim |

Example:

```markdown
---
name: pdf-summarizer
description: Summarize long PDFs into a 5-bullet executive recap.
version: 0.2.0
license: MIT
metadata:
  author: ada@example.com
---

# Instructions

When the user asks about a PDF, fetch it with `pdf_search` first, then …
```

## Install sources

`blueclaw skill install <source> [--project] [--force] [--yes]` accepts three source types:

| Source | Example | Behavior |
|---|---|---|
| Local directory | `~/my-skill` | Copies the directory in place |
| Git URL | `https://github.com/u/r.git` | `git clone --depth=1 --quiet` into a tmp dir, optional `#subdir` for monorepos |
| Direct HTTPS URL to raw `SKILL.md` | `https://example.com/raw/SKILL.md` | Fetches the single file, validates, stages into a tmp dir named after the skill |

Detection is by suffix: a URL ending in `SKILL.md` or `skill.md` is treated as direct-file; everything else with an `http(s)://`, `git@`, `ssh://`, or `git://` prefix is treated as a git URL.

### Install pipeline

1. Resolve the source (clone, fetch, or use in place).
2. Validate via `Skill.from_file(strict=True)`. Strict mode rejects malformed YAML, missing fields, bad name pattern, and dir-name / `name` mismatch.
3. Print a summary (name, description, license, target path) and prompt `Install? [y/N]`. `--yes` skips. Running without `--yes` from a non-TTY (e.g. CI) aborts.
4. Stage to `<target>.__staging__/` then `os.replace` onto the final path. The install is interruption-safe: a crashed run leaves no partial tree, and the next run cleans up the staging dir.

`--force` overwrites an existing skill of the same name in the chosen scope.

## Scope: global vs project

| Scope | Path |
|---|---|
| User-global | `~/blueclaw/skills/<name>/` |
| Per-project | `<project>/.blueclaw/skills/<name>/` |

`<project>` is the directory containing `blueclaw.yaml`, found by walking up from the current working directory. `blueclaw skill install --project` writes to the project scope; the default is global.

At session start, both scopes are walked and merged. **On name collision the project scope wins** — useful for overriding a global skill with a project-specific variant. `blueclaw skill list` shows each skill's resolved scope.

## Managing installed skills

```bash
blueclaw skill list                    # Rich table of installed skills
blueclaw skill list --json             # machine-readable list
blueclaw skill show <name>             # print scope, path, and SKILL.md
blueclaw skill uninstall <name> --yes  # remove from global scope
blueclaw skill uninstall <name> --project --yes  # remove from project scope
```

`uninstall` requires `--yes` in non-TTY environments to prevent accidental deletes from scripts. In an interactive terminal it prompts for confirmation.

## Runtime behavior

Once a skill is installed, every new agent session sees it. The flow:

1. `create_agent(...)` calls `_resolve_skill_paths()` to enumerate skill directories (global + project, project shadowing on collision).
2. If any are found, the agent is constructed with `plugins=[AgentSkills(skills=[...])]`.
3. AgentSkills registers a `skills(skill_name)` tool and, via `BeforeInvocationEvent`, injects an XML block listing every installed skill's name and description into the system prompt.
4. The model can read the index and decide to call `skills("pdf-summarizer")`. The plugin returns the full body text. Subsequent turns can act on those instructions.

The system prompt index only carries `name` + `description`, so token cost scales with the number of skills, not their content. Bodies are loaded on demand.

## Writing a skill

A SKILL.md body is a prompt. The model reads it the moment it calls `skills(<name>)`. Some patterns that work well:

- **Lead with the trigger.** "When the user asks X, do Y." The model only sees the body after committing to activation; you don't need to re-justify why.
- **Refer to tools by their registered names.** If your skill expects `web_search` or a custom MCP tool, name it explicitly — the model uses the body as a tool-selection cue.
- **Keep it shorter than your default system prompt.** Long skill bodies cost tokens every time the skill is activated.
- **Test it.** Use `blueclaw test` (see [testing.md](testing.md)) to lock the behaviour: a YAML spec that asserts the right tool was called after activation is a fast regression guard.

Example body that teaches the agent to summarize a PDF:

```markdown
# pdf-summarizer

When the user asks about a PDF, follow this exact procedure:

1. Identify the file path the user is referring to. Ask once if it is ambiguous; otherwise proceed.
2. Call `pdf_search` to extract the first 2,000 words.
3. Produce a 5-bullet executive summary. Each bullet ≤ 25 words. Lead with the document's central argument.
4. Cite the page number for each bullet using `(p. N)`.

Do **not** fabricate page numbers. If a citation is uncertain, omit it rather than guess.
```

## Validation rules

Validation runs at install (strict) and at session start (lenient — warns and skips invalid skills rather than crashing the session). Both modes share the same checks:

- `SKILL.md` exists (`skill.md` also accepted as a fallback).
- YAML frontmatter is well-formed; unquoted-colon values are auto-recovered.
- `name` and `description` are present and well-formed.
- Directory name equals `name` (strict mode raises, lenient mode logs).

Invalid skills surface in `blueclaw skill install` as `Invalid skill: ...` with the underlying Strands error. At session start, the warning is emitted via the standard `logging` channel — run with `BLUECLAW_LOG_LEVEL=DEBUG` to see it.

## Sharing skills

A skill directory is a self-contained artifact. Three common share patterns:

- **Git repo, one skill per repo.** `blueclaw skill install https://github.com/u/repo.git` clones and installs.
- **Monorepo with a skills directory.** `blueclaw skill install https://github.com/u/repo.git#skills/pdf-summarizer` picks one subdir.
- **Direct file.** Publish a raw `SKILL.md` to a CDN, gist, or static site, and share `blueclaw skill install https://.../SKILL.md`.

There is no central registry. The lookup is whatever URL you publish.

## Trust model

Skills in v2.4 are pure prompt + metadata — no executable code is bundled. The blast radius of a malicious skill is "the system prompt now contains adversarial text," which the existing approval hook, shell deny-list, and workspace sandbox already mitigate. Install still confirms before copying so you can read the metadata before accepting.

If a future v2.x ships Python tools-in-skills (deferred from the v2.4 spec), that surface will require a stricter trust model.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Invalid skill: SKILL.md must start with --- frontmatter delimiter` | Missing or malformed `---` block | Add a complete frontmatter block; YAML is sensitive to leading whitespace |
| `skill name does not match parent directory name` | The directory is named `foo`, frontmatter says `name: bar` | Rename the directory to match the `name` field |
| `git clone failed: ...` | Wrong URL, no network, private repo | Test with `git clone` directly; for private repos use SSH (`git@github.com:...`) |
| `git clone timed out after 30 seconds` | Slow network or huge repo | The timeout is hard-coded at 30 s today; install from a local clone instead |
| `no SKILL.md at <path> (use #subdir for monorepos)` | The git URL points at a repo where `SKILL.md` lives in a subdirectory | Add `#path/to/skill` to the URL |
| `Refusing to install non-interactively without --yes.` | Running install in CI / a script with no TTY | Pass `--yes` once you've audited the source |
| Skill installed but the agent doesn't see it | `--project` was used in a directory with no `blueclaw.yaml`, so the runtime can't find the project root | Install to the global scope, or `blueclaw init` to create `blueclaw.yaml` first |

## Adding to the spec

`AgentSkills.io` extensions like `allowed-tools` are passed through verbatim by Strands. blueclaw does not currently enforce them. If you need an extension, file an issue describing the desired behavior and the standard reference.
