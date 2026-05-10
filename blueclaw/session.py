"""Session management — config, model factory, agent construction, chat loop."""

from __future__ import annotations

import sys
import threading
import time
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from prompt_toolkit import PromptSession
from rich.console import Console
from strands import Agent
from strands.agent.conversation_manager import SummarizingConversationManager

from blueclaw.context import ObservationMaskingManager

# Lazy imports — avoid requiring all provider SDKs at module load
AnthropicModel = None
OllamaModel = None
LiteLLMModel = None

from blueclaw.lessons import build_lessons_block
from blueclaw.models import RunRecord, RunTrace, SessionConfig, calculate_cost
from blueclaw.tools import get_tools, get_mcp_servers
from blueclaw.workspace import Workspace
from blueclaw.approval import ApprovalHooks

logger = logging.getLogger(__name__)


def cleanup_mcp_clients(observer) -> None:
    """Close any MCPClient tools."""
    import warnings

    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=DeprecationWarning)
        for client in getattr(observer, "mcp_clients", []):
            try:
                if hasattr(client, "stop"):
                    client.stop(None, None, None)
                elif hasattr(client, "close"):
                    client.close()
            except Exception:
                pass


def extract_text(value: Any) -> str:
    """Extract plain text from model message payloads."""
    parts: list[str] = []

    def collect(v: Any) -> None:
        if isinstance(v, str):
            text = v.strip()
            if text:
                parts.append(text)
            return

        if isinstance(v, dict):
            content = v.get("content")
            if isinstance(content, list):
                for item in content:
                    collect(item)
                return

            text = v.get("text")
            if isinstance(text, str):
                collect(text)
                return

            message = v.get("message")
            if message is not None:
                collect(message)
            return

        if isinstance(v, list):
            for item in v:
                collect(item)
            return

        text_attr = getattr(v, "text", None)
        if isinstance(text_attr, str):
            collect(text_attr)

    collect(value)
    return "\n".join(parts).strip()


def format_trace_for_explanation(trace) -> str:
    """Format a RunTrace into readable text for LLM explanation."""
    lines = [
        f"Goal: {trace.goal}",
        f"Model: {trace.model_id}",
        f"Status: {trace.status}",
        f"Steps: {len(trace.steps)}",
        "",
    ]
    for step in trace.steps:
        status = f"error: {step.error}" if step.error else step.status
        lines.append(
            f"Step {step.index}: {step.tool_name} ({step.duration_ms}ms) [{status}]"
        )
        if step.input_summary:
            for k, v in step.input_summary.items():
                lines.append(f"  input {k}: {v}")
        if step.output_summary:
            lines.append(f"  output: {step.output_summary}")
        lines.append("")
    return "\n".join(lines)


def is_capability_refusal(text: str) -> bool:
    """Detect model refusal responses that should not overwrite context."""
    lowered = text.lower()
    refusal_signals = (
        "i don't have access to tools",
        "i do not have access to tools",
        "my available tools are limited",
        "can't create or modify files",
        "cannot create or modify files",
    )
    return any(signal in lowered for signal in refusal_signals)


def write_turn_checkpoint(
    workspace: Workspace, goal: str, assistant_message: Any
) -> None:
    """Persist a lightweight checkpoint after each turn for crash recovery."""
    message = extract_text(assistant_message)
    if not message:
        return

    lowered = message.lower()
    if "<system-reminder>" in lowered:
        message = message.split("<system-reminder>", 1)[0]

    lines = [line.rstrip() for line in message.splitlines()]
    deduped: list[str] = []
    for line in lines:
        if deduped and deduped[-1] == line:
            continue
        deduped.append(line)
    cleaned = "\n".join(deduped).strip()
    if not cleaned:
        return

    workspace.write_last_turn_checkpoint(goal=goal, assistant_text=cleaned[:2000])


def load_config(yaml_path: Path, model_override: str | None = None) -> SessionConfig:
    """Load config from YAML file, with optional model override."""
    config_data: dict = {}
    if yaml_path.exists():
        try:
            raw = yaml.safe_load(yaml_path.read_text())
            if isinstance(raw, dict):
                config_data = raw
        except yaml.YAMLError as e:
            raise ValueError(f"Invalid YAML: {e}") from e

    # Flatten model section
    model_section = config_data.get("model", {})
    kwargs: dict = {}
    if "provider" in model_section:
        kwargs["provider"] = model_section["provider"]
    if "model_id" in model_section:
        kwargs["model_id"] = model_section["model_id"]
    if "tools" in config_data:
        kwargs["tools"] = config_data["tools"]
    if "allowlist_domains" in config_data:
        kwargs["allowlist_domains"] = config_data["allowlist_domains"]
    if "workspace" in config_data and "path" in config_data["workspace"]:
        kwargs["workspace_path"] = Path(config_data["workspace"]["path"]).expanduser()
    if (
        "workspace" in config_data
        and "trace_retention_days" in config_data["workspace"]
    ):
        kwargs["trace_retention_days"] = config_data["workspace"][
            "trace_retention_days"
        ]

    server_section = config_data.get("server", {})
    if isinstance(server_section, dict) and "max_concurrent_runs" in server_section:
        kwargs["max_concurrent_runs"] = server_section["max_concurrent_runs"]

    # Context management
    ctx = config_data.get("context", {})
    if isinstance(ctx, dict):
        if "strategy" in ctx:
            kwargs["context_strategy"] = ctx["strategy"]
        if "mask_after" in ctx:
            kwargs["context_mask_after"] = ctx["mask_after"]
        if "summarize_after" in ctx:
            kwargs["context_summarize_after"] = ctx["summarize_after"]

    # Apply model override
    if model_override:
        provider, model_id = parse_model_override(model_override)
        kwargs["provider"] = provider
        kwargs["model_id"] = model_id

    return SessionConfig(**kwargs)


def parse_model_override(override: str) -> tuple[str, str]:
    """Parse 'provider/model_id' string. Bare names raise ValueError."""
    if "/" not in override:
        raise ValueError(
            f"Use 'provider/model_id' format, e.g., 'ollama/llama3'. Got: {override}"
        )
    provider, _, model_id = override.partition("/")
    return provider, model_id


def build_model(config: SessionConfig):
    """Construct the appropriate model from config."""
    if config.provider == "anthropic":
        if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
            raise ValueError(
                "Missing ANTHROPIC_API_KEY for provider 'anthropic'. "
                "Set it in your environment before running blueclaw."
            )
        from strands.models import AnthropicModel

        return AnthropicModel(model_id=config.model_id, max_tokens=config.max_tokens)
    elif config.provider == "ollama":
        from strands.models import OllamaModel

        return OllamaModel(None, model_id=config.model_id)
    elif config.provider == "litellm":
        from strands.models import LiteLLMModel

        return LiteLLMModel(model_id=config.model_id)
    elif config.provider == "openai":
        if not os.environ.get("OPENAI_API_KEY", "").strip():
            raise ValueError(
                "Missing OPENAI_API_KEY for provider 'openai'. "
                "Set it in your environment before running blueclaw."
            )
        from strands.models import OpenAIModel

        return OpenAIModel(model_id=config.model_id)
    else:
        raise ValueError(f"Unknown provider: {config.provider}")


def load_tools(config: SessionConfig, workspace: Workspace | None = None) -> list:
    """Load tools based on config."""
    return get_tools(config.tools, config, workspace=workspace)


def build_system_prompt(
    workspace: Workspace,
    skills_dir: Path | None = None,
    include_history: bool = True,
    channel: str = "terminal",
) -> str:
    """Build system prompt from context, history, and skill index.

    `include_history` should be False when an external session manager
    (e.g. FileSessionManager) is replaying actual messages — otherwise the
    model sees the conversation twice (history narration + replayed turns)
    and tends to recap each reply.

    `channel` selects tone rules. "terminal" assumes plain-text output to a
    TTY (no markdown, no emoji). "api" assumes a chat client that may render
    markdown; rules emphasize brevity and "answer only what was just asked,
    do not recap" because session replay otherwise tempts the model to
    summarize the conversation each turn.
    """
    parts = []

    # Context
    context = workspace.read_context()
    if context:
        parts.append(f"## Persistent Context\n\n{context}")

    # History summary
    if include_history:
        history = workspace.read_history()
        if history:
            parts.append("## Recent History\n")
            for rec in history[-10:]:
                parts.append(
                    f"- [{rec.ts.isoformat()}] {rec.goal} "
                    f"(tools: {', '.join(rec.tools)})"
                )

    # Skill index (names only, not full content)
    if skills_dir and skills_dir.exists():
        skill_files = sorted(skills_dir.glob("*.md"))
        if skill_files:
            parts.append("## Available Skills\n")
            for sf in skill_files:
                name = sf.stem
                # Read first line for description
                first_line = sf.read_text().split("\n")[0].strip("# ").strip()
                parts.append(f"- {name}: {first_line}")

    # Core instructions
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if channel == "api":
        tone_block = (
            "**Tone & style (STRICT — always follow these):**\n"
            "- Be concise. Lead with the answer, not the reasoning.\n"
            "- Answer ONLY what the user just asked in the most recent "
            "message. Do not recap the conversation, summarize earlier "
            "turns, or re-answer questions you have already answered.\n"
            "- No motivational quotes, filler, or cheerful preamble.\n"
            "- No emojis.\n"
            "- Keep responses short. A few sentences is usually enough; "
            "use markdown only when it materially helps the answer.\n"
            "- If you cannot help, say so briefly.\n\n"
        )
    else:
        tone_block = (
            "**Tone & style (STRICT — always follow these):**\n"
            "- Be concise. Lead with the answer or action, not the "
            "reasoning.\n"
            "- NEVER use emojis in responses, even if the context "
            "or history contains them.\n"
            "- No motivational quotes, filler, or cheerful preamble.\n"
            "- No markdown formatting — no **bold**, *italic*, tables, or "
            "headings. Your output is raw text in a terminal; markdown "
            "does not render.\n"
            "- Keep responses short. One or two plain sentences is usually "
            "enough.\n"
            "- If you cannot help, say so briefly.\n\n"
        )
    parts.insert(
        0,
        "You are blueclaw, a terminal automation agent.\n"
        f"Today's date is {today}.\n\n" + tone_block + "**Rules:**\n"
        "- Context below is memory from past sessions, not verified facts. "
        "For anything time-sensitive (what's screening, current prices, availability, "
        "weather, news), always use web_search — never answer from context alone.\n"
        "- CONTEXT.md, history.jsonl, and .blueclaw/ are managed by the system. "
        "Never access them via shell. They are blocked at the sandbox level.\n"
        "- Your persistent context is already loaded below. "
        "It updates automatically on exit.\n"
        "- To remember something, just acknowledge it. To forget, say so — "
        "the exit summarizer will omit it.\n"
        "- Use shell_command for tasks the user asks you to perform, "
        "not for managing your own state.\n"
        "- web_search results include titles, URLs, and snippets. "
        "Use snippets directly when they contain enough information. "
        "Only use http_request to fetch a page if you truly need more detail. "
        "If http_request fails with a domain allowlist error, "
        "do not retry other domains — "
        "answer from the search snippets you already have.",
    )

    return "\n\n".join(parts)


class _StreamingCallback:
    """Stream text to a file object with immediate flush.

    Skips tool headers (observer handles those) and the extra trailing
    newline that the SDK's PrintingCallbackHandler emits on complete.
    """

    def __init__(self, file=None):
        self._file = file or sys.stdout

    def __call__(self, **kwargs):
        data = kwargs.get("data", "")
        complete = kwargs.get("complete", False)
        reasoning = kwargs.get("reasoningText", "")
        if reasoning:
            print(reasoning, end="", file=self._file, flush=True)
        if data:
            print(data, end="\n" if complete else "", file=self._file, flush=True)


_UNSET = object()


def build_trace_and_record(
    result,
    goal: str,
    observer,
    config: SessionConfig,
    run_id: str,
    start_time: datetime,
    end_time: datetime,
    source: str = "terminal",
    conversation_id: str | None = None,
) -> tuple:
    """Build RunTrace and RunRecord from an agent result.

    Pure function — no side effects.
    """
    usage = result.metrics.accumulated_usage
    input_tokens = usage.get("inputTokens", 0)
    output_tokens = usage.get("outputTokens", 0)
    total_tokens = usage.get("totalTokens", 0)
    cache_read_tokens = usage.get("cacheReadInputTokens", 0)
    cache_write_tokens = usage.get("cacheWriteInputTokens", 0)

    cost = calculate_cost(
        config.model_id,
        input_tokens,
        output_tokens,
        cache_read_tokens,
        cache_write_tokens,
    )

    context_masked_chars = None
    context_strategy_val = None
    cm = getattr(observer, "conversation_manager", None)
    if cm is not None and hasattr(cm, "masked_chars"):
        context_masked_chars = cm.masked_chars
        context_strategy_val = config.context_strategy

    trace = RunTrace(
        run_id=run_id,
        goal=goal,
        start_time=start_time,
        end_time=end_time,
        model_id=config.model_id,
        steps=list(observer.trace_steps),
        total_tokens=total_tokens,
        total_cost=cost,
        status="success",
        context_masked_chars=context_masked_chars,
        context_strategy=context_strategy_val,
        source=source,
        conversation_id=conversation_id,
    )

    record = RunRecord(
        ts=end_time,
        goal=goal,
        tools=list(observer.tools_called),
        tokens=total_tokens,
        cost=cost,
        conversation_id=conversation_id,
    )

    return trace, record


def create_agent(
    config: SessionConfig,
    workspace: Workspace,
    observer,
    model=None,
    skills_dir: Path | None = None,
    scripted: bool = False,
    console: Console | None = None,
    callback_handler=_UNSET,
    session_manager=None,
    channel: str = "terminal",
) -> Agent:
    """Construct and return a Strands Agent."""
    tools = load_tools(config, workspace=workspace)
    mcp_clients = get_mcp_servers(config)
    tools.extend(mcp_clients)

    system_prompt = build_system_prompt(
        workspace,
        skills_dir=skills_dir,
        include_history=session_manager is None,
        channel=channel,
    )
    approval_hooks = ApprovalHooks(config, scripted=scripted)

    # Build conversation manager based on config
    if config.context_strategy == "mask":
        conversation_manager = ObservationMaskingManager(
            mask_after=config.context_mask_after,
        )
    elif config.context_strategy == "hybrid":
        conversation_manager = ObservationMaskingManager(
            mask_after=config.context_mask_after,
            summarize_after=config.context_summarize_after or 43,
        )
    else:  # "summarize" — legacy behavior
        conversation_manager = SummarizingConversationManager()

    stream_file = console.file if console else sys.stdout
    resolved_callback = (
        _StreamingCallback(file=stream_file)
        if callback_handler is _UNSET
        else callback_handler
    )
    agent_kwargs = dict(
        model=model,
        tools=tools,
        hooks=[approval_hooks, observer],
        system_prompt=system_prompt,
        conversation_manager=conversation_manager,
        callback_handler=resolved_callback,
    )
    if session_manager is not None:
        agent_kwargs["session_manager"] = session_manager
    agent = Agent(**agent_kwargs)
    # Attach refs for cleanup and metrics
    observer.mcp_clients = mcp_clients
    observer.conversation_manager = conversation_manager
    return agent


def _snapshot_messages(messages: list) -> str:
    """Convert message list to a readable string for summarization."""
    parts = []
    for msg in messages:
        role = msg.get("role", "unknown")
        text = extract_text(msg)
        if text:
            parts.append(f"{role}: {text[:500]}")
    return "\n\n".join(parts)


def update_context_background(model, messages_snapshot: str, workspace: Workspace):
    """Update CONTEXT.md using a fresh Agent (no shared state)."""
    current_context = workspace.read_context()
    try:
        summary_agent = Agent(
            model=model,
            tools=[],
            system_prompt=(
                "You update a persistent context file. "
                "Keep only durable facts: user preferences, "
                "project state, workspace setup. "
                "EXCLUDE transient data: recommendations given, "
                "weather, prices, news, "
                "search results, documents read, commands run, "
                "and anything time-sensitive. "
                "Be concise. Return only markdown. "
                "No tool mentions or policies."
            ),
            callback_handler=None,
        )
        result = summary_agent(
            f"Current CONTEXT.md:\n\n{current_context}\n\n"
            f"Recent conversation:\n\n{messages_snapshot}\n\n"
            "Update CONTEXT.md with any new facts from the conversation. "
            "Preserve existing facts that are still relevant."
        )
        text = extract_text(getattr(result, "message", result))
        if text and not is_capability_refusal(text):
            workspace.write_context(text)
            workspace.clear_last_turn_checkpoint()
    except Exception as e:
        logger.debug("Background context update failed: %s", e)


class BackgroundContextUpdater:
    def __init__(self, model, workspace: Workspace):
        self.model = model
        self.workspace = workspace
        self._thread: threading.Thread | None = None

    def trigger(self, agent):
        """Snapshot messages and start background update."""
        if self._thread and self._thread.is_alive():
            return
        snapshot = _snapshot_messages(agent.messages[-20:])
        self._thread = threading.Thread(
            target=update_context_background,
            args=(self.model, snapshot, self.workspace),
            daemon=False,
        )
        self._thread.start()

    def wait(self, timeout=15.0):
        """Wait for background update to finish."""
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)


def run_chat_loop(
    agent,
    workspace: Workspace,
    observer,
    console: Console,
    config: SessionConfig,
    model=None,
    scripted: bool = False,
) -> None:
    """Run the interactive chat loop."""
    exit_commands = {
        "exit",
        "quit",
        "/exit",
        "/quit",
        "eixt",
        "exti",
        "exiit",
        "ext",
        "exi",
        "bye",
        "q",
    }
    session = PromptSession()
    turn_count = 0
    total_tool_calls = 0
    updater = BackgroundContextUpdater(model, workspace) if model else None

    try:
        while True:
            try:
                user_input = session.prompt("blueclaw> ")
            except KeyboardInterrupt:
                break
            except EOFError:
                break

            stripped = user_input.strip()
            if not stripped:
                continue
            if stripped.lower() in exit_commands:
                break

            turn_count += 1

            # Parse @<path> attachments before composing the agent prompt.
            from blueclaw.uploads import build_agent_input, parse_at_attachments

            cleaned_message, attachments = parse_at_attachments(stripped)
            for att in attachments:
                console.print(
                    f"[dim]attached:[/dim] {att.path} "
                    f"({att.mime_type})"
                )

            # Inject trace lessons for this goal
            prompt_text = cleaned_message
            try:
                traces = workspace.list_traces(limit=50)
                lessons = build_lessons_block(cleaned_message, traces)
                if lessons:
                    prompt_text = f"{lessons}\n\n{cleaned_message}"
            except Exception:
                pass

            agent_input = build_agent_input(attachments, prompt_text)

            start = time.time()
            result = agent(agent_input)
            elapsed = time.time() - start
            total_tool_calls += len(observer.tools_called)

            # Strands streams the response via callback — don't reprint

            try:
                write_turn_checkpoint(workspace, stripped, result.message)
            except Exception as e:
                logger.debug("Failed to write turn checkpoint: %s", e)

            print_run_summary(
                result=result,
                goal=stripped,
                observer=observer,
                workspace=workspace,
                config=config,
                console=console,
                elapsed=elapsed,
                start_time=start,
            )

            if updater and (turn_count >= 2 or total_tool_calls > 0):
                updater.trigger(agent)
    except Exception:
        pass
    finally:
        if updater and turn_count > 0 and (turn_count >= 2 or total_tool_calls > 0):
            updater.wait()
        cleanup_mcp_clients(observer)


def print_run_summary(
    result,
    goal: str,
    observer,
    workspace: Workspace,
    config: SessionConfig,
    console: Console,
    elapsed: float = 0.0,
    start_time: float | None = None,
) -> None:
    """Print end-of-run summary and record to history + trace."""
    now = datetime.now(timezone.utc)
    usage = result.metrics.accumulated_usage
    total_tokens = usage.get("totalTokens", 0)
    cost = calculate_cost(
        config.model_id,
        usage.get("inputTokens", 0),
        usage.get("outputTokens", 0),
    )
    steps = len(observer.tools_called)

    # Ensure summary starts on a new line after streamed response
    console.print()

    # Build summary line
    parts = [f"Done \u00b7 {steps} steps \u00b7 {total_tokens} tokens"]
    if cost is not None:
        parts.append(f"${cost:.4f}")
    if elapsed > 0:
        parts.append(f"{elapsed:.1f}s")
    console.print(" \u00b7 ".join(parts))

    run_start = (
        datetime.fromtimestamp(start_time, tz=timezone.utc) if start_time else now
    )
    run_id = run_start.strftime("%Y%m%d-%H%M%S")

    trace, record = build_trace_and_record(
        result, goal, observer, config, run_id, run_start, now, source="terminal"
    )

    # Reset context metrics after capturing them in build_trace_and_record
    cm = getattr(observer, "conversation_manager", None)
    if cm is not None and hasattr(cm, "reset_metrics"):
        cm.reset_metrics()

    workspace.write_trace(trace)
    workspace.append_history(record)

    # observer.reset() stays here — NOT in build_trace_and_record
    # (API handler discards observer without reset)
    observer.reset()


def update_context_on_exit(agent, workspace: Workspace) -> None:
    """Update CONTEXT.md via agent summarization."""
    current_context = workspace.read_context()

    try:
        # Suppress streaming output during summarization
        original_callback = getattr(agent, "callback_handler", None)
        if original_callback is not None:
            agent.callback_handler = lambda **_: None
        try:
            result = agent(
                "Update CONTEXT.md with key facts from this session. "
                "Keep only durable facts: user preferences, "
                "project state, workspace setup. "
                "EXCLUDE transient data: recommendations given, "
                "weather, prices, news, "
                "search results, documents read, commands run, "
                "and anything time-sensitive. "
                "Not a timeline. Be concise. "
                "Return only markdown for CONTEXT.md. Do not call any tools. "
                "Do not mention tool limitations, capabilities, or policies."
            )
        finally:
            if original_callback is not None:
                agent.callback_handler = original_callback

        raw_message = getattr(result, "message", result)
        context_text = extract_text(raw_message)
        if context_text and not is_capability_refusal(context_text):
            workspace.write_context(context_text)
            workspace.clear_last_turn_checkpoint()
        else:
            if current_context:
                workspace.write_context(current_context)
    except Exception as e:
        logger.debug("Failed to update CONTEXT.md on exit: %s", e)
        if current_context:
            workspace.write_context(current_context)
