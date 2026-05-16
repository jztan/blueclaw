# syntax=docker/dockerfile:1.7
FROM python:3.12-slim

# Runtime utilities the agent needs at minimum.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        git \
        ripgrep \
    && rm -rf /var/lib/apt/lists/*

# Create a non-root user. uid/gid are placeholders — the launcher overrides at runtime
# with --user $(id -u):$(id -g). The HOME directory must therefore be world-writable
# for the parts that the agent writes to (skills cache, etc.), or read-only where it
# doesn't.
RUN useradd --create-home --home-dir /home/blueclaw --shell /bin/bash --uid 1000 blueclaw \
    && mkdir -p /home/blueclaw/skills /home/blueclaw/blueclaw/workspace /workspace /project \
    && chmod 0755 /home/blueclaw /home/blueclaw/blueclaw /home/blueclaw/blueclaw/workspace /home/blueclaw/skills \
    && chmod 0777 /workspace /project

# Install blueclaw and its dependencies system-wide so any --user can import it.
# In editable installs the launcher overlays /opt/blueclaw-src via PYTHONPATH at runtime.
WORKDIR /tmp/build
COPY pyproject.toml uv.lock* README.md ./
COPY blueclaw ./blueclaw
# Install with ALL model-provider extras so the image works regardless of
# which provider the user configures. Adds ~50MB; acceptable for a runtime
# image that's meant to be provider-agnostic.
RUN pip install --no-cache-dir --root-user-action=ignore ".[anthropic,ollama,openai,gemini]" \
    && rm -rf /tmp/build

# Ensure imports work from any HOME at runtime.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/home/blueclaw

WORKDIR /workspace
ENTRYPOINT ["blueclaw"]
