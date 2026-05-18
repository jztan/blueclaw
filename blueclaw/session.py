"""Session management — config, model factory, agent construction, chat loop."""

from __future__ import annotations

import sys
import threading
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

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

if TYPE_CHECKING:
    from blueclaw.runner import RunOutcome

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

    # Flatten model section (also accept top-level provider/model_id).
    model_section = config_data.get("model", {})
    kwargs: dict = {}
    if "provider" in model_section:
        kwargs["provider"] = model_section["provider"]
    elif "provider" in config_data:
        kwargs["provider"] = config_data["provider"]
    if "model_id" in model_section:
        kwargs["model_id"] = model_section["model_id"]
    elif "model_id" in config_data:
        kwargs["model_id"] = config_data["model_id"]
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

    # Sandbox section — Pydantic constructs SandboxConfig from the nested dict.
    if "sandbox" in config_data and isinstance(config_data["sandbox"], dict):
        kwargs["sandbox"] = config_data["sandbox"]

    # Context management
    ctx = config_data.get("context", {})
    if isinstance(ctx, dict):
        if "strategy" in ctx:
            kwargs["context_strategy"] = ctx["strategy"]
        if "mask_after" in ctx:
            kwargs["context_mask_after"] = ctx["mask_after"]
        if "summarize_after" in ctx:
            kwargs["context_summarize_after"] = ctx["summarize_after"]

    bridges_section = config_data.get("bridges", {})
    if isinstance(bridges_section, dict):
        kwargs["bridges"] = bridges_section

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


def _resolve_skill_paths() -> list:
    """Return the list of skill dirs to feed AgentSkills (test seam)."""
    from blueclaw.skills import (
        default_global_dir,
        default_project_dir,
        resolved_skill_paths,
    )

    return resolved_skill_paths(
        global_dir=default_global_dir(),
        project_dir=default_project_dir(),
    )


def build_system_prompt(
    workspace: Workspace,
    include_history: bool = True,
    channel: str = "terminal",
) -> str:
    """Build system prompt from context and history.

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

    # Identity (SOUL.md — persona/voice, user-managed, optional)
    soul = workspace.read_soul()
    if soul:
        parts.append(f"## Identity\n\n{soul}")

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

    # Core instructions
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if channel == "api":
        tone_block = (
            "**Tone & style (STRICT — always follow these):**\n"
            "- Be concise. Lead with the answer, not the reasoning.\n"
            "- Do not recap the conversation or re-answer questions "
            "you've already answered. But DO carry forward earlier "
            "constraints, deliverables, and corrections — if turn 1 "
            'asked for a 3-course menu and turn 2 adds "make it '
            'vegan," the answer must still be a 3-course menu. If '
            "turn 2 corrects an error from turn 1, subsequent turns "
            "must reflect the correction.\n"
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
        "If http_request fails with a domain allowlist error, do not "
        "silently fan out to other domains on your own. But if the user "
        "explicitly asks you to retry, says the domain is now allowlisted, "
        "or supplies a new URL, retry the tool — the allowlist may have "
        "been updated between turns. Only fall back to search snippets "
        "after a fresh tool call confirms the block still stands.\n"
        "- Your available tools are defined in the tool schemas attached "
        "to this conversation. Before stating you cannot do something, "
        "you must either (a) invoke a tool that could plausibly do it, "
        "or (b) name the specific missing capability. \"I don't have "
        'access to real-time data" is not acceptable when a web search '
        "tool is attached.\n"
        "- If you can only fulfill part of a request, the response must "
        "(1) attempt every part that is possible, (2) explicitly name "
        "each part you cannot fulfill and why, and (3) not substitute "
        "unrelated content (extra tips, checklists, formatting) for the "
        "missing parts.\n"
        "- When the user corrects a factual error you made, acknowledge "
        "the correction in the first sentence of your reply before "
        "continuing.\n"
        "- Do not use formatting (tables, headers, bullet lists, emoji, "
        "boxed notes) to compensate for missing substance. If the "
        "substantive answer is short, the response should be short. "
        "Polish does not substitute for completeness.",
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

    # Skills plugin (Strands 1.30+)
    plugins = []
    skill_paths = _resolve_skill_paths()
    if skill_paths:
        from strands.vended_plugins.skills import AgentSkills

        plugins.append(AgentSkills(skills=[str(p) for p in skill_paths]))

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
        plugins=plugins,
        system_prompt=system_prompt,
        conversation_manager=conversation_manager,
        callback_handler=resolved_callback,
    )
    if session_manager is not None:
        agent_kwargs["session_manager"] = session_manager
    agent = Agent(**agent_kwargs)
    # FileSessionManager rehydrates agent.state, including the skills plugin's
    # `last_injected_xml`. But we rebuild the system prompt fresh each turn, so
    # that XML is never present. Clearing the key makes the plugin treat the
    # next invocation as a first injection (no spurious "unable to find
    # previously injected skills XML" warning).
    if session_manager is not None and plugins:
        agent.state.set("agent_skills", {})
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
    workspace: Workspace,
    console: Console,
    config: SessionConfig,
    model=None,
    scripted: bool = False,
) -> None:
    """Run the interactive chat loop.

    The chat loop holds a single runner_session across many turns —
    agent.messages accumulates conversation history because terminal has
    no FileSessionManager. Each turn invokes ctx.agent(...) then calls
    finalize (or finalize_error on adapter-caught exception). The
    context manager's __exit__ runs MCP cleanup once at loop end.
    """
    import secrets

    from blueclaw.runner import (
        bus_for_turn,
        finalize,
        finalize_error,
        next_capture_path,
        runner_session,
    )

    session_id = (
        f"{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
        f"-{secrets.token_hex(2)}"
    )

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
    prompt_session = PromptSession()
    turn_count = 0
    total_tool_calls = 0

    with runner_session(
        config,
        workspace,
        model,
        callback_handler=_StreamingCallback(file=console.file),
        channel="terminal",
        scripted=scripted,
        observer_console=console,
        observer_quiet=False,
    ) as ctx:
        updater = BackgroundContextUpdater(model, workspace) if model else None
        try:
            while True:
                try:
                    user_input = prompt_session.prompt("blueclaw> ")
                except (KeyboardInterrupt, EOFError):
                    break

                stripped = user_input.strip()
                if not stripped:
                    continue
                if stripped.lower() in exit_commands:
                    break

                turn_count += 1

                from blueclaw.uploads import (
                    UploadError,
                    build_agent_input,
                    parse_at_attachments,
                )

                cleaned_message, attachments, failed = parse_at_attachments(stripped)
                for att in attachments:
                    console.print(f"[dim]attached:[/dim] {att.path} ({att.mime_type})")
                for token, reason in failed:
                    console.print(
                        f"[yellow]could not attach[/yellow] {token}: {reason}"
                    )

                prompt_text = cleaned_message
                try:
                    agent_input = build_agent_input(attachments, prompt_text)
                except UploadError as exc:
                    console.print(f"[yellow]could not attach:[/yellow] {exc}")
                    turn_count -= 1
                    continue

                start_time = datetime.now(timezone.utc)
                capture_path = next_capture_path(workspace.root, session_id)
                with bus_for_turn(ctx.observer, capture_path):
                    try:
                        try:
                            traces = workspace.list_traces(limit=50)

                            def on_lessons_injected(stats: dict) -> None:
                                b = getattr(ctx.observer, "bus", None)
                                if b is not None:
                                    b.emit({"type": "lesson.injected", **stats})

                            lessons = build_lessons_block(
                                cleaned_message,
                                traces,
                                on_injected=on_lessons_injected,
                            )
                            if lessons:
                                prompt_text = f"{lessons}\n\n{cleaned_message}"
                                agent_input = build_agent_input(
                                    attachments, prompt_text
                                )
                        except Exception:
                            pass

                        result = ctx.agent(agent_input)
                        end_time = datetime.now(timezone.utc)
                        outcome = finalize(
                            ctx,
                            result,
                            goal=stripped,
                            source="terminal",
                            conversation_id=session_id,
                            start_time=start_time,
                            end_time=end_time,
                            config=config,
                            capture_path=capture_path,
                            workspace_root=workspace.root,
                        )
                        total_tool_calls += len(outcome.record.tools)
                        elapsed = (end_time - start_time).total_seconds()

                        try:
                            write_turn_checkpoint(workspace, stripped, result.message)
                        except Exception as e:
                            logger.debug("Failed to write turn checkpoint: %s", e)

                        print_run_summary(outcome, console=console, elapsed=elapsed)
                        workspace.write_trace(outcome.trace)
                        workspace.append_history(outcome.record)

                        cm = getattr(ctx.observer, "conversation_manager", None)
                        if cm is not None and hasattr(cm, "reset_metrics"):
                            cm.reset_metrics()
                        ctx.observer.reset()

                        if updater and (turn_count >= 2 or total_tool_calls > 0):
                            updater.trigger(ctx.agent)
                    except Exception as exc:
                        end_time = datetime.now(timezone.utc)
                        console.print(f"[red]agent error:[/red] {exc}")
                        finalize_error(
                            ctx,
                            exc,
                            goal=stripped,
                            source="terminal",
                            conversation_id=session_id,
                            start_time=start_time,
                            end_time=end_time,
                            config=config,
                            capture_path=capture_path,
                            workspace_root=workspace.root,
                        )
                        turn_count -= 1
                        continue
        except Exception as exc:
            console.print(f"[red]session error:[/red] {exc}")
        finally:
            if updater and turn_count > 0 and (turn_count >= 2 or total_tool_calls > 0):
                updater.wait()


def print_run_summary(
    outcome: "RunOutcome",
    *,
    console: Console,
    elapsed: float,
) -> None:
    """Print the end-of-turn summary line for terminal.

    The runner builds the trace and record; the adapter persists them.
    This function only prints — no I/O against the workspace, no
    metric reset on the observer (which now lives inside the runner).
    """
    record = outcome.record
    if record is None:
        # finalize_error path — adapter handles the error printout separately.
        return

    console.print()  # newline after streamed response
    parts = [f"Done · {len(record.tools)} steps · {record.tokens} tokens"]
    if record.cost is not None:
        parts.append(f"${record.cost:.4f}")
    if elapsed > 0:
        parts.append(f"{elapsed:.1f}s")
    console.print(" · ".join(parts))


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
