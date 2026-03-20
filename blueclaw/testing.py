"""Test spec loading, runner, assertions, formatters, and stub tools."""

from __future__ import annotations

import time
from collections import defaultdict
from io import StringIO
from math import sqrt
from pathlib import Path
from xml.etree import ElementTree as ET

import yaml
from rich.console import Console

from blueclaw.models import TestCase, TestResult, TestSpec, calculate_cost
from blueclaw.observer import ObserverHooks
from blueclaw.session import (
    build_model,
    build_system_prompt,
    cleanup_mcp_clients,
    create_agent,
    extract_text,
)
from blueclaw.workspace import Workspace
from strands import Agent, tool

# --- Spec loading & validation ---


def load_spec(path: Path) -> TestSpec:
    """Load a test spec from a YAML file."""
    text = Path(path).read_text()
    data = yaml.safe_load(text)
    return TestSpec.model_validate(data)


def validate_spec(spec: TestSpec) -> list[str]:
    """Validate a test spec, returning a list of warnings."""
    warnings = []
    if not spec.tests:
        warnings.append("No tests defined")
    for i, case in enumerate(spec.tests):
        if not case.goal.strip():
            warnings.append(f"Test {i + 1}: empty goal")
        if case.runs <= 0:
            warnings.append(f"Test {i + 1}: runs must be > 0")
        if not (0 <= case.threshold <= 1):
            warnings.append(f"Test {i + 1}: threshold must be between 0 and 1")
        if case.max_cost is not None and case.max_cost < 0:
            warnings.append(f"Test {i + 1}: negative max_cost")
    if spec.model and "/" not in spec.model:
        warnings.append(
            f"Model '{spec.model}' missing provider prefix (e.g. anthropic/...)"
        )
    return warnings


# --- Wilson CI & assertions ---


def wilson_ci(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score 95% CI for binomial proportion."""
    if n == 0:
        return (0.0, 1.0)
    p = successes / n
    denom = 1 + z**2 / n
    center = (p + z**2 / (2 * n)) / denom
    margin = z * sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / denom
    return (max(0.0, center - margin), min(1.0, center + margin))


def _check_assertions(
    case: TestCase,
    tools_called: list[str],
    response_text: str,
    step_count: int,
    cost: float | None,
) -> list[str]:
    """Check assertions for a test case, returning failure messages."""
    failures = []
    if case.expected_tools:
        missing = set(case.expected_tools) - set(tools_called)
        if missing:
            failures.append(f"Missing tools: {', '.join(sorted(missing))}")
    if case.expected_output_contains is not None:
        if case.expected_output_contains.lower() not in response_text.lower():
            failures.append(
                f"Output does not contain: '{case.expected_output_contains}'"
            )
    if case.max_steps is not None and step_count > case.max_steps:
        failures.append(f"Too many steps: {step_count} > {case.max_steps}")
    if case.max_cost is not None:
        if cost is None:
            failures.append("Cost unknown (model not in pricing table)")
        elif cost > case.max_cost:
            failures.append(f"Cost exceeded: ${cost:.4f} > ${case.max_cost}")
    return failures


# --- Single run ---


def _run_single(
    case: TestCase,
    config,
    workspace_path: Path,
    model,
) -> TestResult:
    """Execute a single agent run for a test case."""
    workspace = Workspace(workspace_path)
    quiet_console = Console(file=StringIO())
    observer = ObserverHooks(console=quiet_console, quiet=True)
    start = time.time()
    try:
        agent = create_agent(
            config,
            workspace,
            observer,
            model=model,
            scripted=True,
            console=quiet_console,
        )
        result = agent(case.goal)
        elapsed = time.time() - start

        usage = result.metrics.accumulated_usage
        input_tokens = usage.get("inputTokens", 0)
        output_tokens = usage.get("outputTokens", 0)
        cost = calculate_cost(config.model_id, input_tokens, output_tokens)
        step_count = len(observer.tools_called)
        tools_called = list(observer.tools_called)
        response_text = extract_text(getattr(result, "message", result))

        failures = _check_assertions(
            case, tools_called, response_text, step_count, cost
        )
        passed = len(failures) == 0
        return TestResult(
            goal=case.goal,
            passed=passed,
            verdict="pass" if passed else "fail",
            failures=failures,
            tools_called=tools_called,
            steps=step_count,
            cost=cost,
            duration_s=elapsed,
        )
    except Exception as e:
        return TestResult(
            goal=case.goal,
            passed=False,
            verdict="fail",
            error=str(e),
            duration_s=time.time() - start,
        )
    finally:
        cleanup_mcp_clients(observer)


# --- Orchestration ---


def run_test_case(
    case: TestCase,
    config,
    workspace_dir: Path,
    model,
) -> TestResult:
    """Run a test case (single or multi-run with Wilson CI)."""
    if case.runs <= 1:
        return _run_single(case, config, workspace_dir, model)

    pass_count = 0
    all_failures: list[str] = []
    known_costs: list[float] = []
    total_duration = 0.0
    last_result: TestResult | None = None
    for r in range(case.runs):
        result = _run_single(case, config, Path(workspace_dir) / f"run-{r:03d}", model)
        last_result = result
        if result.passed:
            pass_count += 1
        else:
            all_failures.extend(result.failures)
            if result.error:
                all_failures.append(f"Error: {result.error}")
        if result.cost is not None:
            known_costs.append(result.cost)
        total_duration += result.duration_s

    lower, upper = wilson_ci(pass_count, case.runs)
    if lower >= case.threshold:
        verdict = "pass"
    elif upper < case.threshold:
        verdict = "fail"
    else:
        verdict = "inconclusive"

    return TestResult(
        goal=case.goal,
        passed=(verdict == "pass"),
        verdict=verdict,
        pass_count=pass_count,
        total_runs=case.runs,
        ci_lower=lower,
        ci_upper=upper,
        failures=all_failures[:5],
        cost=sum(known_costs) if known_costs else None,
        duration_s=total_duration,
        tools_called=last_result.tools_called if last_result else [],
        steps=last_result.steps if last_result else 0,
    )


def run_spec(spec: TestSpec, config, workspace_dir: Path) -> list[TestResult]:
    """Run all test cases in a spec."""
    progress = Console(stderr=True)
    model = build_model(config)
    results = []
    for i, case in enumerate(spec.tests):
        label = case.goal[:40] + ("..." if len(case.goal) > 40 else "")
        progress.print(f"Test {i + 1}/{len(spec.tests)}: {label}", end="")
        ws = Path(workspace_dir) / f"case-{i:03d}"
        result = run_test_case(case, config, ws, model)
        results.append(result)
        progress.print(f" ...{result.verdict.upper()}")
    return results


# --- Output formatters ---


def format_tap(results: list[TestResult]) -> str:
    """Format results as TAP version 13."""
    lines = ["TAP version 13", f"1..{len(results)}"]
    for i, r in enumerate(results, 1):
        goal = r.goal.replace("\n", " ")
        if r.verdict == "inconclusive":
            ci = (
                f"[{r.ci_lower:.2f}, {r.ci_upper:.2f}]"
                if r.ci_lower is not None
                else ""
            )
            lines.append(
                f"ok {i} - {goal} "
                f"# INCONCLUSIVE {r.pass_count}/{r.total_runs} passed {ci}"
            )
        elif r.verdict == "pass":
            lines.append(f"ok {i} - {goal}")
        else:
            lines.append(f"not ok {i} - {goal}")
            if r.failures:
                lines.append("  ---")
                lines.append("  failures:")
                for f in r.failures:
                    lines.append(f'    - "{f}"')
                lines.append("  ...")
            elif r.error:
                lines.append("  ---")
                lines.append(f'  error: "{r.error}"')
                lines.append("  ...")
    return "\n".join(lines) + "\n"


def format_junit(results: list[TestResult]) -> str:
    """Format results as JUnit XML."""
    suite = ET.Element(
        "testsuite",
        name="blueclaw",
        tests=str(len(results)),
        failures=str(
            sum(1 for r in results if r.failures and r.verdict != "inconclusive")
        ),
        errors=str(
            sum(
                1
                for r in results
                if r.error and not r.failures and r.verdict != "inconclusive"
            )
        ),
        skipped=str(sum(1 for r in results if r.verdict == "inconclusive")),
        time=f"{sum(r.duration_s for r in results):.1f}",
    )
    for r in results:
        tc = ET.SubElement(suite, "testcase", name=r.goal, time=f"{r.duration_s:.1f}")
        if r.verdict == "inconclusive":
            msg = f"INCONCLUSIVE {r.pass_count}/{r.total_runs} passed"
            if r.ci_lower is not None:
                msg += f" [{r.ci_lower:.2f}, {r.ci_upper:.2f}]"
            ET.SubElement(tc, "skipped", message=msg)
        elif r.error and not r.failures:
            ET.SubElement(tc, "error", message=r.error, type="Exception")
        elif r.failures:
            ET.SubElement(tc, "failure", message="; ".join(r.failures))
    root = ET.Element("testsuites")
    root.append(suite)
    ET.indent(root, space="  ")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        + ET.tostring(root, encoding="unicode")
        + "\n"
    )


# --- Stub tools & replay ---


def make_stub_tools(trace):
    """Create @tool stubs from a recorded trace."""
    outputs_by_tool = defaultdict(list)
    for step in trace.steps:
        outputs_by_tool[step.tool_name].append(
            step.output_summary or "[no output recorded]"
        )

    call_counters: dict[str, int] = defaultdict(int)
    call_log: list[str] = []

    def _make_one_stub(name, outputs):
        @tool(name=name, description=f"Stub for {name}")
        def stub_fn(**kwargs):
            idx = call_counters[name]
            call_counters[name] += 1
            call_log.append(name)
            if idx < len(outputs):
                return outputs[idx]
            return "[stub: no more recorded outputs]"

        return stub_fn

    tools = [_make_one_stub(n, o) for n, o in outputs_by_tool.items()]
    return tools, call_log


def run_stub_replay(trace, config) -> None:
    """Re-run model reasoning with recorded tool outputs (stub tools only)."""
    model = build_model(config)
    stub_tools, call_log = make_stub_tools(trace)
    workspace = Workspace(config.workspace_path)
    system_prompt = build_system_prompt(workspace)
    agent = Agent(model=model, tools=stub_tools, system_prompt=system_prompt)
    agent(trace.goal)

    original = [s.tool_name for s in trace.steps]
    if call_log == original:
        print(f"Original: {' -> '.join(original)}")
        print(f"Replayed: {' -> '.join(call_log)}")
        print("Result: MATCH (same tool sequence)")
    else:
        print(f"Original: {' -> '.join(original)}")
        print(f"Replayed: {' -> '.join(call_log)}")
        print("Result: DIVERGED (different tool sequence)")
