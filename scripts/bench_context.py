#!/usr/bin/env python3
"""Benchmark context management strategies (mask vs summarize).

Runs a multi-turn prompt sequence through a single agent session,
measuring per-turn tokens, cost, and masked chars.  Compares strategies
side-by-side when run in 'compare' mode (default).

Usage:
    python scripts/bench_context.py scripts/prompts/rust-vs-go.yaml
    python scripts/bench_context.py prompts.yaml --strategy mask
    python scripts/bench_context.py prompts.yaml --output results.json
    python scripts/bench_context.py prompts.yaml --model anthropic/claude-sonnet-4-6
"""

import argparse
import json
import tempfile
import time
import warnings
from io import StringIO
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
warnings.filterwarnings("ignore", category=UserWarning, module="pydantic")

import yaml
from rich.console import Console

from blueclaw.models import calculate_cost
from blueclaw.observer import ObserverHooks
from blueclaw.session import (
    build_model,
    cleanup_mcp_clients,
    create_agent,
    extract_text,
    load_config,
    print_run_summary,
)
from blueclaw.workspace import Workspace


def parse_args():
    parser = argparse.ArgumentParser(
        description="Benchmark context management strategies",
    )
    parser.add_argument("prompts", help="Path to YAML prompt file")
    parser.add_argument(
        "--strategy",
        default="compare",
        choices=["mask", "summarize", "hybrid", "compare"],
        help="Strategy to test (default: compare runs mask + summarize)",
    )
    parser.add_argument(
        "--model", default=None, help="Model override (provider/model_id)"
    )
    parser.add_argument("--output", default=None, help="Save JSON results to file")
    parser.add_argument(
        "--mask-after",
        type=int,
        default=None,
        help="Override mask window size M (default 10)",
    )
    return parser.parse_args()


def run_session(config, prompts, workspace_dir):
    """Run all prompts in a single agent session, return per-turn metrics."""
    workspace = Workspace(workspace_dir)
    console = Console(file=StringIO())
    observer = ObserverHooks(console=console, quiet=True)
    model = build_model(config)
    agent = create_agent(
        config, workspace, observer, model=model, scripted=True, console=console
    )

    results = []
    prev_usage = {"inputTokens": 0, "outputTokens": 0, "totalTokens": 0}

    for i, prompt in enumerate(prompts, 1):
        label = prompt[:40] + ("..." if len(prompt) > 40 else "")
        try:
            start = time.time()
            result = agent(prompt)
            elapsed = time.time() - start

            # Delta tracking — accumulated_usage is cumulative
            usage = result.metrics.accumulated_usage
            turn_input = usage.get("inputTokens", 0) - prev_usage.get("inputTokens", 0)
            turn_output = usage.get("outputTokens", 0) - prev_usage.get(
                "outputTokens", 0
            )
            turn_total = usage.get("totalTokens", 0) - prev_usage.get("totalTokens", 0)
            prev_usage = dict(usage)

            cm = getattr(observer, "conversation_manager", None)
            masked = cm.masked_chars if cm and hasattr(cm, "masked_chars") else 0

            cost = calculate_cost(config.model_id, turn_input, turn_output)
            steps = len(observer.tools_called)
            response = extract_text(getattr(result, "message", result))

            results.append(
                {
                    "prompt": prompt,
                    "tokens": turn_total,
                    "input_tokens": turn_input,
                    "output_tokens": turn_output,
                    "cost": cost,
                    "steps": steps,
                    "elapsed": elapsed,
                    "masked_chars": masked,
                    "response": response,
                }
            )

            cost_str = f"${cost:.3f}" if cost is not None else "n/a"
            print(
                f"Turn {i:2d}: {label}  "
                f"{steps} steps · {turn_total:,} tokens · "
                f"{cost_str} · {elapsed:.1f}s · masked {masked:,} chars"
            )

            print_run_summary(
                result=result,
                goal=prompt,
                observer=observer,
                workspace=workspace,
                config=config,
                console=console,
                elapsed=elapsed,
                start_time=start,
            )

        except Exception as e:
            print(f"Turn {i:2d}: {label}  [ERROR] {e}")
            # Sync prev_usage so next turn's delta is correct.
            # Strands only updates accumulated_usage after successful
            # model calls, so failed turns add nothing — but we sync
            # to keep prev_usage consistent with agent state.
            try:
                prev_usage = dict(agent.event_loop_metrics.accumulated_usage)
            except Exception:
                pass
            observer.reset()
            cm = getattr(observer, "conversation_manager", None)
            if cm and hasattr(cm, "reset_metrics"):
                cm.reset_metrics()
            results.append(
                {
                    "prompt": prompt,
                    "tokens": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cost": None,
                    "steps": 0,
                    "elapsed": 0,
                    "masked_chars": 0,
                    "error": str(e),
                }
            )

    cleanup_mcp_clients(observer)
    return results


def print_subtotal(strategy, results):
    """Print subtotal line for a strategy run."""
    total_steps = sum(r["steps"] for r in results)
    total_tokens = sum(r["tokens"] for r in results)
    total_cost = sum(r["cost"] for r in results if r["cost"] is not None)
    total_time = sum(r["elapsed"] for r in results)
    total_masked = sum(r["masked_chars"] for r in results)
    errors = sum(1 for r in results if "error" in r)

    parts = [
        f"Subtotal: {total_steps} steps",
        f"{total_tokens:,} tokens",
        f"${total_cost:.3f}",
        f"{total_time:.1f}s",
        f"{total_masked:,} chars masked",
    ]
    if errors:
        parts.append(f"{errors} errors")
    print(" · ".join(parts))


def print_comparison(all_results):
    """Print side-by-side comparison table."""
    print("=== Comparison ===")
    print(f"{'':14s} ", end="")
    for strategy in all_results:
        print(f"{strategy:>12s}  ", end="")
    if len(all_results) == 2:
        print(f"{'delta':>10s}", end="")
    print()

    strategies = list(all_results.keys())

    def total(strategy, key):
        return sum(r[key] for r in all_results[strategy] if r.get(key) is not None)

    metrics = [
        ("Tokens", "tokens", "{:,}"),
        ("Cost", "cost", "${:.3f}"),
        ("Steps", "steps", "{}"),
        ("Time", "elapsed", "{:.1f}s"),
        ("Masked chars", "masked_chars", "{:,}"),
    ]

    for label, key, fmt in metrics:
        print(f"{label:14s} ", end="")
        values = []
        for s in strategies:
            v = total(s, key)
            values.append(v)
            formatted = fmt.format(v)
            print(f"{formatted:>12s}  ", end="")
        if len(values) == 2 and values[1] > 0:
            delta = (values[0] - values[1]) / values[1] * 100
            print(f"{delta:>+9.1f}%", end="")
        print()

    # Print last turn's response for each strategy (quality comparison)
    print()
    for s in strategies:
        turns = all_results[s]
        last = turns[-1]
        resp = last.get("response", "")
        if resp:
            print(f"--- {s} final response ({len(resp)} chars) ---")
            print(resp[:2000])
            if len(resp) > 2000:
                print(f"... [truncated, {len(resp)} total chars]")
            print()


def main():
    args = parse_args()
    spec = yaml.safe_load(Path(args.prompts).read_text())
    prompts = spec["prompts"]
    name = spec.get("name", args.prompts)

    config = load_config(Path("blueclaw.yaml"), model_override=args.model)
    if args.mask_after is not None:
        config.context_mask_after = args.mask_after

    strategies = (
        [args.strategy] if args.strategy != "compare" else ["mask", "summarize"]
    )

    bench_dir = Path(tempfile.mkdtemp(prefix="blueclaw-bench-"))
    print(f"Benchmark: {name}")
    print(f"Prompts: {len(prompts)}, Strategies: {strategies}")
    print(f"Model: {config.model_id}")
    print(f"Workspace: {bench_dir}\n")

    all_results = {}
    for strategy in strategies:
        config.context_strategy = strategy
        mask_info = (
            f" (mask_after={config.context_mask_after})"
            if strategy in ("mask", "hybrid")
            else ""
        )
        print(f"=== Strategy: {strategy}{mask_info} ===")
        results = run_session(config, prompts, bench_dir / strategy)
        all_results[strategy] = results
        print_subtotal(strategy, results)
        print()

    if len(all_results) > 1:
        print_comparison(all_results)

    if args.output:
        Path(args.output).write_text(json.dumps(all_results, indent=2, default=str))
        print(f"\nResults saved to {args.output}")

    print(f"\nTraces in: {bench_dir}")
    print("Cleanup: rm -rf", bench_dir)


if __name__ == "__main__":
    main()
