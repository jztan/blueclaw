"""Test spec loading, runner, assertions, formatters, and stub tools."""

from __future__ import annotations

import os
import re
import secrets
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
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


def _artifacts_root(artifacts_root: Path | None = None) -> Path | None:
    """Resolve the artifacts root and create the per-invocation directory.

    Precedence: function param > BLUECLAW_ARTIFACTS_ROOT env > ~/blueclaw/test-runs/.
    Returns the path to the per-invocation directory (already created), or
    None if creation failed. On failure, prints one stderr warning.
    """
    if artifacts_root is not None:
        root = Path(artifacts_root)
    elif os.environ.get("BLUECLAW_ARTIFACTS_ROOT"):
        root = Path(os.environ["BLUECLAW_ARTIFACTS_ROOT"])
    else:
        root = Path.home() / "blueclaw" / "test-runs"

    now = datetime.now(timezone.utc)
    ts = (
        now.strftime("%Y%m%dT%H%M%S")
        + f"{now.microsecond // 1000:03d}Z-{secrets.token_hex(2)}"
    )
    invocation_dir = root / ts
    try:
        invocation_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(
            f"blueclaw test: artifact capture disabled — "
            f"could not create {invocation_dir}: {e}",
            file=sys.stderr,
        )
        return None
    return invocation_dir


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
        contradictory = set(case.expected_tools) & set(case.forbidden_tools)
        if contradictory:
            joined = ", ".join(sorted(contradictory))
            warnings.append(
                f"Test {i + 1}: tool(s) in both expected and forbidden: {joined}"
            )
        if case.output_regex is not None:
            try:
                re.compile(case.output_regex)
            except re.error as e:
                warnings.append(f"Test {i + 1}: invalid output_regex: {e}")
        if case.forbidden_output_regex is not None:
            try:
                re.compile(case.forbidden_output_regex)
            except re.error as e:
                warnings.append(f"Test {i + 1}: invalid forbidden_output_regex: {e}")
        if case.max_duration_s is not None and case.max_duration_s <= 0:
            warnings.append(f"Test {i + 1}: max_duration_s must be > 0")
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


def _validate_workspace_file(
    workspace_root: Path, f: str, assertion_name: str
) -> tuple[Path | None, str | None]:
    """Resolve and validate a file path within workspace.

    Returns (resolved_path, error_message). If error_message is set, resolved_path
    is None. Caller must check workspace_root is not None before calling.
    """
    resolved = (workspace_root / f).resolve()
    ws_prefix = str(workspace_root.resolve()) + os.sep
    if not str(resolved).startswith(ws_prefix) and resolved != workspace_root.resolve():
        return None, f"{assertion_name}: path outside workspace: {f}"
    return resolved, None


def _check_assertions(
    case: TestCase,
    tools_called: list[str],
    response_text: str,
    step_count: int,
    cost: float | None,
    duration_s: float = 0.0,
    workspace_root: Path | None = None,
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

    # --- New assertions ---

    if case.forbidden_tools:
        called = set(case.forbidden_tools) & set(tools_called)
        if called:
            failures.append(f"Forbidden tools called: {', '.join(sorted(called))}")

    if case.expected_files:
        if workspace_root is None:
            failures.append("expected_files requires workspace context")
        else:
            for f in case.expected_files:
                resolved, err = _validate_workspace_file(
                    workspace_root, f, "expected_files"
                )
                if err:
                    failures.append(err)
                    continue
                if not resolved.exists():
                    failures.append(f"expected_files: file not found: {f}")

    if case.expected_file_contains:
        if workspace_root is None:
            failures.append("expected_file_contains requires workspace context")
        else:
            for f, substring in case.expected_file_contains.items():
                resolved, err = _validate_workspace_file(
                    workspace_root, f, "expected_file_contains"
                )
                if err:
                    failures.append(err)
                    continue
                if not resolved.exists():
                    failures.append(f"expected_file_contains: file not found: {f}")
                    continue
                try:
                    content = resolved.read_text(errors="replace")
                except OSError as e:
                    failures.append(f"expected_file_contains: cannot read '{f}': {e}")
                    continue
                if substring.lower() not in content.lower():
                    failures.append(
                        f"expected_file_contains: '{f}' does not contain:"
                        f" '{substring}'"
                    )

    if case.forbidden_output_contains is not None:
        if case.forbidden_output_contains.lower() in response_text.lower():
            failures.append(
                f"Output contains forbidden text:"
                f" '{case.forbidden_output_contains}'"
            )

    if case.output_regex is not None:
        try:
            if not re.search(case.output_regex, response_text):
                failures.append(f"Output does not match regex: '{case.output_regex}'")
        except re.error as e:
            failures.append(f"Invalid regex: {case.output_regex}: {e}")

    if case.forbidden_output_regex is not None:
        try:
            if re.search(case.forbidden_output_regex, response_text):
                failures.append(
                    f"Output matches forbidden regex:"
                    f" '{case.forbidden_output_regex}'"
                )
        except re.error as e:
            failures.append(
                f"Invalid forbidden regex: {case.forbidden_output_regex}: {e}"
            )

    if case.tool_order:
        it = iter(tools_called)
        for expected in case.tool_order:
            if expected not in it:
                failures.append(
                    f"Tool order violation: expected {case.tool_order} in order"
                )
                break

    if case.max_duration_s is not None:
        if duration_s > case.max_duration_s:
            failures.append(
                f"Duration exceeded: {duration_s:.1f}s > {case.max_duration_s}s"
            )

    return failures


# --- Single run ---


def _write_run_result(workspace_path: Path, result: TestResult) -> None:
    """Write per-run result JSON for --keep-workspace inspection."""
    try:
        out = workspace_path / ".blueclaw" / "result.json"
        out.write_text(result.model_dump_json(indent=2))
    except OSError:
        pass  # Best-effort; don't fail the test over a diagnostic write


def _write_artifacts(
    invocation_dir: Path,
    case_idx: int,
    run_idx: int,
    response_text: str,
    messages: list,
) -> list[dict]:
    """Write response.txt + messages.json for one run.

    Returns a list of capture-failure records (empty on success). Each record:
        {"case_idx": int, "run_idx": int, "stage": str, "reason": str}
    where stage is one of "mkdir" | "response.txt" | "messages.json".
    """
    import json

    failures: list[dict] = []
    run_dir = invocation_dir / f"case-{case_idx:03d}" / f"run-{run_idx:03d}"
    try:
        run_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        failures.append(
            {
                "case_idx": case_idx,
                "run_idx": run_idx,
                "stage": "mkdir",
                "reason": f"{type(e).__name__}: {e}",
            }
        )
        print(
            f"blueclaw test: capture failure case-{case_idx:03d}/run-{run_idx:03d}: "
            f"mkdir: {e}",
            file=sys.stderr,
        )
        return failures

    # Attempt both files independently so one failure does not block the other.
    try:
        (run_dir / "response.txt").write_text(response_text)
    except OSError as e:
        failures.append(
            {
                "case_idx": case_idx,
                "run_idx": run_idx,
                "stage": "response.txt",
                "reason": f"{type(e).__name__}: {e}",
            }
        )
        print(
            f"blueclaw test: capture failure case-{case_idx:03d}/run-{run_idx:03d}: "
            f"response.txt: {e}",
            file=sys.stderr,
        )

    try:
        (run_dir / "messages.json").write_text(
            json.dumps(messages, indent=2, default=str)
        )
    except OSError as e:
        failures.append(
            {
                "case_idx": case_idx,
                "run_idx": run_idx,
                "stage": "messages.json",
                "reason": f"{type(e).__name__}: {e}",
            }
        )
        print(
            f"blueclaw test: capture failure case-{case_idx:03d}/run-{run_idx:03d}: "
            f"messages.json: {e}",
            file=sys.stderr,
        )

    return failures


def _run_single(
    case: TestCase,
    config,
    workspace_path: Path,
    model,
    invocation_dir: Path | None = None,
    case_idx: int = 0,
    run_idx: int = 0,
    capture_failures: list[dict] | None = None,
) -> TestResult:
    """Execute a single agent run for a test case.

    If invocation_dir is provided, writes response.txt + messages.json to
    invocation_dir/case-<NNN>/run-<NNN>/. Any capture failures are appended
    to capture_failures (which the caller owns).
    """
    if capture_failures is None:
        capture_failures = []
    workspace = Workspace(workspace_path)
    quiet_console = Console(file=StringIO())
    observer = ObserverHooks(console=quiet_console, quiet=True)
    start = time.time()
    result = None
    agent = None
    error_str: str | None = None
    try:
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
        except Exception as e:
            error_str = str(e)
        elapsed = time.time() - start

        if result is not None:
            usage = result.metrics.accumulated_usage
            input_tokens = usage.get("inputTokens", 0)
            output_tokens = usage.get("outputTokens", 0)
            cache_read_tokens = usage.get("cacheReadInputTokens", 0)
            cache_write_tokens = usage.get("cacheWriteInputTokens", 0)
            cost = calculate_cost(
                config.model_id,
                input_tokens,
                output_tokens,
                cache_read_tokens,
                cache_write_tokens,
            )
            step_count = len(observer.tools_called)
            tools_called = list(observer.tools_called)
            response_text = (
                extract_text(result.message)
                if getattr(result, "message", None) is not None
                else ""
            )

            failures = _check_assertions(
                case,
                tools_called,
                response_text,
                step_count,
                cost,
                duration_s=elapsed,
                workspace_root=workspace.root,
            )
            passed = len(failures) == 0
            verdict = "pass" if passed else "fail"
        else:
            # Agent exception path — no result available
            response_text = ""
            tools_called = []
            step_count = 0
            cost = None
            failures = []
            passed = False
            verdict = "fail"

        # Capture artifacts before constructing TestResult (need artifacts_path)
        artifacts_path: str | None = None
        if invocation_dir is not None:
            cap_errs = _write_artifacts(
                invocation_dir,
                case_idx,
                run_idx,
                response_text,
                list(getattr(agent, "messages", [])),
            )
            capture_failures.extend(cap_errs)
            # Always record the intended path even if some files failed to write;
            # the existence of capture_failures entries flags partial captures.
            artifacts_path = str(
                invocation_dir / f"case-{case_idx:03d}" / f"run-{run_idx:03d}"
            )

        test_result = TestResult(
            goal=case.goal,
            passed=passed,
            verdict=verdict,
            failures=failures,
            tools_called=tools_called,
            steps=step_count,
            cost=cost,
            duration_s=elapsed,
            error=error_str,
            artifacts_path=artifacts_path,
        )
        _write_run_result(workspace_path, test_result)
        return test_result
    finally:
        cleanup_mcp_clients(observer)


# --- Orchestration ---


def run_test_case(
    case: TestCase,
    config,
    workspace_dir: Path,
    model,
    invocation_dir: Path | None = None,
    case_idx: int = 0,
    capture_failures: list[dict] | None = None,
) -> TestResult:
    """Run a test case (single or multi-run with Wilson CI)."""
    if capture_failures is None:
        capture_failures = []
    if case.runs <= 1:
        return _run_single(
            case,
            config,
            workspace_dir,
            model,
            invocation_dir=invocation_dir,
            case_idx=case_idx,
            run_idx=0,
            capture_failures=capture_failures,
        )

    pass_count = 0
    all_failures: list[str] = []
    known_costs: list[float] = []
    total_duration = 0.0
    last_result: TestResult | None = None
    for r in range(case.runs):
        result = _run_single(
            case,
            config,
            Path(workspace_dir) / f"run-{r:03d}",
            model,
            invocation_dir=invocation_dir,
            case_idx=case_idx,
            run_idx=r,
            capture_failures=capture_failures,
        )
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
        # For multi-run, surface the parent case-NNN/ dir so triage points
        # at the whole case, not the last individual run.
        artifacts_path=(
            str(invocation_dir / f"case-{case_idx:03d}")
            if invocation_dir is not None
            else None
        ),
    )


def _write_invocation_metadata(
    invocation_dir: Path,
    spec: TestSpec,
    config,
    results: list[TestResult],
    capture_failures: list[dict],
) -> None:
    """Write invocation.json summarizing the run.

    Best-effort: a write failure here is logged to stderr but does not
    fail the eval.
    """
    import json
    from blueclaw import __version__ as blueclaw_version

    summary = {
        "pass": sum(1 for r in results if r.verdict == "pass"),
        "fail": sum(1 for r in results if r.verdict == "fail"),
        "inconclusive": sum(1 for r in results if r.verdict == "inconclusive"),
    }
    known_costs = [r.cost for r in results if r.cost is not None]
    meta = {
        "timestamp": invocation_dir.name,
        "timestamp_format": "UTC compact: YYYYMMDDTHHMMSSfffZ-<4hex>",
        "spec_path": getattr(spec, "_spec_path", None),
        "model": config.model_id,
        "blueclaw_version": blueclaw_version,
        "argv": list(sys.argv),
        "total_cost_usd": sum(known_costs) if known_costs else None,
        "summary": summary,
        "capture_failures": capture_failures,
    }
    try:
        (invocation_dir / "invocation.json").write_text(
            json.dumps(meta, indent=2, default=str)
        )
    except OSError as e:
        print(
            f"blueclaw test: failed to write invocation.json: {e}",
            file=sys.stderr,
        )


def run_spec(
    spec: TestSpec,
    config,
    workspace_dir: Path,
    artifacts_root: Path | None = None,
) -> tuple[list[TestResult], Path | None]:
    """Run all test cases in a spec.

    Returns (results, invocation_dir). invocation_dir is None if artifact
    capture was disabled (creation failed). If capture succeeds, writes
    invocation.json after all runs complete. Capture failures are
    best-effort and do not fail the run.
    """
    progress = Console(stderr=True)
    model = build_model(config)
    invocation_dir = _artifacts_root(artifacts_root=artifacts_root)
    capture_failures: list[dict] = []
    results = []
    for i, case in enumerate(spec.tests):
        label = case.goal[:40] + ("..." if len(case.goal) > 40 else "")
        progress.print(f"Test {i + 1}/{len(spec.tests)}: {label}", end="")
        ws = Path(workspace_dir) / f"case-{i:03d}"
        result = run_test_case(
            case,
            config,
            ws,
            model,
            invocation_dir=invocation_dir,
            case_idx=i,
            capture_failures=capture_failures,
        )
        results.append(result)
        progress.print(f" ...{result.verdict.upper()}")

    if invocation_dir is not None:
        _write_invocation_metadata(
            invocation_dir, spec, config, results, capture_failures
        )

    return results, invocation_dir


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
            if r.failures:
                lines.append("  ---")
                lines.append("  failures:")
                for f in r.failures:
                    lines.append(f'    - "{f}"')
                lines.append("  ...")
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
            if r.failures:
                msg += "; " + "; ".join(r.failures)
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
