"""Tests for blueclaw.testing — spec loading, assertions, formatters, runner."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch
from xml.etree import ElementTree as ET

import pytest

from blueclaw.models import TestCase, TestResult, TestSpec

# --- Fixtures ---


@pytest.fixture
def sample_test_case():
    return TestCase(
        goal="create hello.txt",
        expected_tools=["shell_command"],
        expected_output_contains="hello.txt",
        max_steps=5,
        max_cost=0.01,
    )


@pytest.fixture
def sample_test_spec(tmp_path):
    spec_path = tmp_path / "spec.yaml"
    spec_path.write_text(
        "tests:\n"
        "  - goal: create hello.txt\n"
        "    expected_tools: [shell_command]\n"
        "model: anthropic/claude-haiku-4-5-20251001\n"
    )
    return spec_path


# --- Group A: Pure functions ---


class TestLoadSpec:
    def test_load_spec_valid(self, sample_test_spec):
        from blueclaw.testing import load_spec

        spec = load_spec(sample_test_spec)
        assert len(spec.tests) == 1
        assert spec.tests[0].goal == "create hello.txt"
        assert spec.tests[0].expected_tools == ["shell_command"]
        assert spec.model == "anthropic/claude-haiku-4-5-20251001"

    def test_load_spec_missing_goal(self, tmp_path):
        from blueclaw.testing import load_spec

        path = tmp_path / "bad.yaml"
        path.write_text("tests:\n  - expected_tools: [shell_command]\n")
        with pytest.raises(Exception):
            load_spec(path)


class TestValidateSpec:
    def test_validate_spec_empty_tests(self):
        from blueclaw.testing import validate_spec

        spec = TestSpec(tests=[])
        warnings = validate_spec(spec)
        assert any("No tests" in w for w in warnings)

    def test_validate_spec_negative_cost(self):
        from blueclaw.testing import validate_spec

        spec = TestSpec(tests=[TestCase(goal="test", max_cost=-1.0)])
        warnings = validate_spec(spec)
        assert any("negative max_cost" in w for w in warnings)

    def test_validate_spec_bad_model_format(self):
        from blueclaw.testing import validate_spec

        spec = TestSpec(tests=[TestCase(goal="test")], model="bare-model")
        warnings = validate_spec(spec)
        assert any("provider prefix" in w for w in warnings)

    def test_validate_spec_runs_zero(self):
        from blueclaw.testing import validate_spec

        spec = TestSpec(tests=[TestCase(goal="test", runs=0)])
        warnings = validate_spec(spec)
        assert any("runs must be > 0" in w for w in warnings)

    def test_validate_spec_threshold_out_of_range(self):
        from blueclaw.testing import validate_spec

        spec = TestSpec(tests=[TestCase(goal="test", threshold=1.5)])
        warnings = validate_spec(spec)
        assert any("threshold" in w for w in warnings)

    def test_validate_spec_empty_goal(self):
        from blueclaw.testing import validate_spec

        spec = TestSpec(tests=[TestCase(goal="   ")])
        warnings = validate_spec(spec)
        assert any("empty goal" in w for w in warnings)

    def test_validate_spec_valid(self):
        from blueclaw.testing import validate_spec

        spec = TestSpec(
            tests=[TestCase(goal="test")],
            model="anthropic/claude-haiku-4-5-20251001",
        )
        warnings = validate_spec(spec)
        assert warnings == []


class TestWilsonCI:
    def test_wilson_ci_all_pass(self):
        from blueclaw.testing import wilson_ci

        lower, upper = wilson_ci(10, 10)
        assert lower > 0.65
        assert upper == 1.0

    def test_wilson_ci_all_fail(self):
        from blueclaw.testing import wilson_ci

        lower, upper = wilson_ci(0, 10)
        assert lower == 0.0
        assert upper < 0.35

    def test_wilson_ci_mixed(self):
        from blueclaw.testing import wilson_ci

        lower, upper = wilson_ci(8, 10)
        assert 0.4 < lower < 0.9
        assert 0.85 < upper <= 1.0


class TestCheckAssertions:
    def test_expected_tools_subset_pass(self, sample_test_case):
        from blueclaw.testing import _check_assertions

        failures = _check_assertions(
            sample_test_case,
            ["web_search", "shell_command"],
            "created hello.txt",
            3,
            0.005,
        )
        assert failures == []

    def test_expected_tools_subset_fail(self, sample_test_case):
        from blueclaw.testing import _check_assertions

        failures = _check_assertions(
            sample_test_case, ["web_search"], "created hello.txt", 3, 0.005
        )
        assert any("Missing tools" in f for f in failures)

    def test_output_contains_pass(self, sample_test_case):
        from blueclaw.testing import _check_assertions

        failures = _check_assertions(
            sample_test_case, ["shell_command"], "Created hello.txt ok", 3, 0.005
        )
        assert not any("Output does not contain" in f for f in failures)

    def test_output_contains_fail(self, sample_test_case):
        from blueclaw.testing import _check_assertions

        failures = _check_assertions(
            sample_test_case, ["shell_command"], "done", 3, 0.005
        )
        assert any("Output does not contain" in f for f in failures)

    def test_max_steps_pass(self, sample_test_case):
        from blueclaw.testing import _check_assertions

        failures = _check_assertions(
            sample_test_case, ["shell_command"], "hello.txt", 5, 0.005
        )
        assert not any("Too many steps" in f for f in failures)

    def test_max_steps_fail(self, sample_test_case):
        from blueclaw.testing import _check_assertions

        failures = _check_assertions(
            sample_test_case, ["shell_command"], "hello.txt", 6, 0.005
        )
        assert any("Too many steps" in f for f in failures)

    def test_max_cost_pass(self, sample_test_case):
        from blueclaw.testing import _check_assertions

        failures = _check_assertions(
            sample_test_case, ["shell_command"], "hello.txt", 3, 0.005
        )
        assert not any("Cost" in f for f in failures)

    def test_max_cost_fail(self, sample_test_case):
        from blueclaw.testing import _check_assertions

        failures = _check_assertions(
            sample_test_case, ["shell_command"], "hello.txt", 3, 0.02
        )
        assert any("Cost exceeded" in f for f in failures)

    def test_max_cost_unknown_model(self, sample_test_case):
        from blueclaw.testing import _check_assertions

        failures = _check_assertions(
            sample_test_case, ["shell_command"], "hello.txt", 3, None
        )
        assert any("Cost unknown" in f for f in failures)

    def test_multiple_failures(self, sample_test_case):
        from blueclaw.testing import _check_assertions

        failures = _check_assertions(sample_test_case, ["web_search"], "done", 10, 0.05)
        assert len(failures) >= 3


# --- Group B: Output formatters ---


class TestFormatTAP:
    def test_format_tap_pass(self):
        from blueclaw.testing import format_tap

        results = [TestResult(goal="test goal", passed=True, verdict="pass")]
        output = format_tap(results)
        assert "TAP version 13" in output
        assert "1..1" in output
        assert "ok 1 - test goal" in output

    def test_format_tap_fail(self):
        from blueclaw.testing import format_tap

        results = [
            TestResult(
                goal="test goal",
                passed=False,
                verdict="fail",
                failures=["Missing tools: shell_command"],
            )
        ]
        output = format_tap(results)
        assert "not ok 1 - test goal" in output

    def test_format_tap_error_diagnostic(self):
        from blueclaw.testing import format_tap

        results = [
            TestResult(
                goal="test goal",
                passed=False,
                verdict="fail",
                error="ConnectionTimeout",
            )
        ]
        output = format_tap(results)
        assert 'error: "ConnectionTimeout"' in output

    def test_format_tap_failure_diagnostic(self):
        from blueclaw.testing import format_tap

        results = [
            TestResult(
                goal="test goal",
                passed=False,
                verdict="fail",
                failures=["Missing tools: fetch_url"],
            )
        ]
        output = format_tap(results)
        assert "failures:" in output
        assert '"Missing tools: fetch_url"' in output

    def test_format_tap_inconclusive(self):
        from blueclaw.testing import format_tap

        results = [
            TestResult(
                goal="test goal",
                passed=False,
                verdict="inconclusive",
                pass_count=24,
                total_runs=30,
                ci_lower=0.63,
                ci_upper=0.93,
            )
        ]
        output = format_tap(results)
        assert "INCONCLUSIVE" in output
        assert "24/30" in output
        assert "[0.63, 0.93]" in output


class TestFormatJUnit:
    def test_format_junit_valid_xml(self):
        from blueclaw.testing import format_junit

        results = [TestResult(goal="test", passed=True, verdict="pass")]
        output = format_junit(results)
        root = ET.fromstring(output)
        assert root.tag == "testsuites"

    def test_format_junit_failure_elements(self):
        from blueclaw.testing import format_junit

        results = [
            TestResult(
                goal="test",
                passed=False,
                verdict="fail",
                failures=["Missing tools: x"],
            )
        ]
        output = format_junit(results)
        root = ET.fromstring(output)
        failure = root.find(".//failure")
        assert failure is not None
        assert "Missing tools: x" in failure.attrib["message"]

    def test_format_junit_error_elements(self):
        from blueclaw.testing import format_junit

        results = [
            TestResult(
                goal="test",
                passed=False,
                verdict="fail",
                error="Timeout",
                failures=["Missing tools: x"],
            )
        ]
        output = format_junit(results)
        root = ET.fromstring(output)
        # Should have failure, not error (failures take precedence)
        failure = root.find(".//failure")
        assert failure is not None

    def test_format_junit_error_without_failure(self):
        from blueclaw.testing import format_junit

        results = [
            TestResult(
                goal="test",
                passed=False,
                verdict="fail",
                error="Crash!",
            )
        ]
        output = format_junit(results)
        root = ET.fromstring(output)
        error = root.find(".//error")
        assert error is not None
        assert error.attrib["message"] == "Crash!"
        assert error.attrib["type"] == "Exception"
        # No failure element
        assert root.find(".//failure") is None

    def test_format_junit_inconclusive_skipped(self):
        from blueclaw.testing import format_junit

        results = [
            TestResult(
                goal="test",
                passed=False,
                verdict="inconclusive",
                pass_count=24,
                total_runs=30,
                ci_lower=0.63,
                ci_upper=0.93,
            )
        ]
        output = format_junit(results)
        root = ET.fromstring(output)
        skipped = root.find(".//skipped")
        assert skipped is not None
        assert "INCONCLUSIVE" in skipped.attrib["message"]

    def test_format_junit_suite_counts(self):
        from blueclaw.testing import format_junit

        results = [
            TestResult(goal="pass", passed=True, verdict="pass"),
            TestResult(
                goal="fail",
                passed=False,
                verdict="fail",
                failures=["bad"],
            ),
            TestResult(
                goal="error",
                passed=False,
                verdict="fail",
                error="boom",
            ),
            TestResult(
                goal="skip",
                passed=False,
                verdict="inconclusive",
                pass_count=5,
                total_runs=10,
            ),
        ]
        output = format_junit(results)
        root = ET.fromstring(output)
        suite = root.find("testsuite")
        assert suite.attrib["tests"] == "4"
        assert suite.attrib["failures"] == "1"
        assert suite.attrib["errors"] == "1"
        assert suite.attrib["skipped"] == "1"


# --- Group C: Stub tools ---


class TestStubTools:
    def test_make_stub_tools_count(self, sample_trace):
        from blueclaw.testing import make_stub_tools

        tools, _ = make_stub_tools(sample_trace)
        # 2 unique tools in sample_trace: web_search, http_request
        assert len(tools) == 2

    def test_stub_returns_recorded_output(self, sample_trace):
        from blueclaw.testing import make_stub_tools

        tools, call_log = make_stub_tools(sample_trace)
        # Find web_search stub
        ws_stub = next(t for t in tools if t.tool_name == "web_search")
        result = ws_stub(query="test")
        assert "Found 10 results" in result
        assert call_log == ["web_search"]

    def test_stub_exhausted(self, sample_trace):
        from blueclaw.testing import make_stub_tools

        tools, _ = make_stub_tools(sample_trace)
        ws_stub = next(t for t in tools if t.tool_name == "web_search")
        ws_stub(query="first")  # consume the one recorded output
        result = ws_stub(query="second")  # exhausted
        assert "no more recorded outputs" in result


# --- Group D: Integration with mocked agent ---


def _make_mock_agent_result(tools_called=None):
    """Create a mock agent result with realistic structure."""
    result = MagicMock()
    result.message = {"content": [{"text": "created hello.txt successfully"}]}
    result.metrics.accumulated_usage = {
        "inputTokens": 100,
        "outputTokens": 50,
    }
    return result


class TestRunTestCase:
    @patch("blueclaw.testing.create_agent")
    @patch("blueclaw.testing.build_model")
    def test_run_test_case_mock_agent(self, mock_bm, mock_ca, sample_config):
        from blueclaw.testing import run_test_case

        mock_agent = MagicMock()
        mock_agent.return_value = _make_mock_agent_result()
        mock_ca.return_value = mock_agent

        case = TestCase(
            goal="create hello.txt",
            expected_output_contains="hello.txt",
        )
        result = run_test_case(case, sample_config, Path("/tmp/test"), None)
        assert result.passed
        assert result.verdict == "pass"

    @patch("blueclaw.testing.create_agent")
    @patch("blueclaw.testing.build_model")
    def test_run_spec_all_results(self, mock_bm, mock_ca, sample_config):
        from blueclaw.testing import run_spec

        mock_agent = MagicMock()
        mock_agent.return_value = _make_mock_agent_result()
        mock_ca.return_value = mock_agent

        spec = TestSpec(
            tests=[
                TestCase(goal="test 1"),
                TestCase(goal="test 2"),
                TestCase(goal="test 3"),
            ]
        )
        results = run_spec(spec, sample_config, Path("/tmp/test"))
        assert len(results) == 3


class TestMultiRun:
    @patch("blueclaw.testing.create_agent")
    def test_multi_run_pass(self, mock_ca, sample_config):
        """30/30 at threshold 0.85 -> pass (Wilson CI lower > 0.85)."""
        from blueclaw.testing import run_test_case

        def make_agent(*args, **kwargs):
            agent = MagicMock()
            agent.return_value = _make_mock_agent_result()
            return agent

        mock_ca.side_effect = make_agent

        case = TestCase(
            goal="create hello.txt",
            expected_output_contains="hello.txt",
            runs=30,
            threshold=0.85,
        )
        result = run_test_case(case, sample_config, Path("/tmp/test"), None)
        assert result.verdict == "pass"
        assert result.pass_count == 30

    @patch("blueclaw.testing.create_agent")
    def test_multi_run_fail(self, mock_ca, sample_config):
        """5/30 at threshold 0.85 -> fail."""
        from blueclaw.testing import run_test_case

        call_count = 0

        def make_agent(*args, **kwargs):
            nonlocal call_count
            agent = MagicMock()
            call_count += 1
            result = _make_mock_agent_result()
            if call_count > 5:
                result.message = {"content": [{"text": "failed"}]}
            agent.return_value = result
            return agent

        mock_ca.side_effect = make_agent

        case = TestCase(
            goal="create hello.txt",
            expected_output_contains="hello.txt",
            runs=30,
            threshold=0.85,
        )
        result = run_test_case(case, sample_config, Path("/tmp/test"), None)
        assert result.verdict == "fail"
        assert result.pass_count == 5

    @patch("blueclaw.testing.create_agent")
    def test_multi_run_inconclusive(self, mock_ca, sample_config):
        """24/30 at threshold 0.85 -> inconclusive."""
        from blueclaw.testing import run_test_case

        call_count = 0

        def make_agent(*args, **kwargs):
            nonlocal call_count
            agent = MagicMock()
            call_count += 1
            result = _make_mock_agent_result()
            if call_count > 24:
                result.message = {"content": [{"text": "failed"}]}
            agent.return_value = result
            return agent

        mock_ca.side_effect = make_agent

        case = TestCase(
            goal="create hello.txt",
            expected_output_contains="hello.txt",
            runs=30,
            threshold=0.85,
        )
        result = run_test_case(case, sample_config, Path("/tmp/test"), None)
        assert result.verdict == "inconclusive"
        assert result.pass_count == 24

    @patch("blueclaw.testing.create_agent")
    def test_multi_run_cost_none(self, mock_ca, sample_config):
        """All runs return cost=None -> aggregate cost is None."""
        from blueclaw.testing import run_test_case

        # Use model not in pricing table
        sample_config.model_id = "unknown-model"

        def make_agent(*args, **kwargs):
            agent = MagicMock()
            agent.return_value = _make_mock_agent_result()
            return agent

        mock_ca.side_effect = make_agent

        case = TestCase(goal="test", runs=3)
        result = run_test_case(case, sample_config, Path("/tmp/test"), None)
        assert result.cost is None

    @patch("blueclaw.testing.create_agent")
    def test_multi_run_populates_tools_from_last_run(self, mock_ca, sample_config):
        """tools_called and steps come from last run."""
        from blueclaw.testing import run_test_case

        def make_agent(*args, **kwargs):
            agent = MagicMock()
            observer = args[2]  # observer is the 3rd positional arg
            observer.tools_called.append("shell_command")
            agent.return_value = _make_mock_agent_result()
            return agent

        mock_ca.side_effect = make_agent

        case = TestCase(goal="test", runs=3)
        result = run_test_case(case, sample_config, Path("/tmp/test"), None)
        assert "shell_command" in result.tools_called

    @patch("blueclaw.testing.create_agent")
    def test_multi_run_error_propagation(self, mock_ca, sample_config):
        """Crashed runs add 'Error: ...' to aggregate failures list."""
        from blueclaw.testing import run_test_case

        call_count = 0

        def make_agent(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            agent = MagicMock()
            if call_count == 2:
                agent.side_effect = RuntimeError("boom")
            else:
                agent.return_value = _make_mock_agent_result()
            return agent

        mock_ca.side_effect = make_agent

        case = TestCase(goal="test", runs=3)
        result = run_test_case(case, sample_config, Path("/tmp/test"), None)
        assert any("Error: boom" in f for f in result.failures)

    @patch("blueclaw.testing.create_agent")
    def test_single_run_ignores_threshold(self, mock_ca, sample_config):
        """runs=1 uses binary pass/fail, ignoring threshold."""
        from blueclaw.testing import run_test_case

        def make_agent(*args, **kwargs):
            agent = MagicMock()
            agent.return_value = _make_mock_agent_result()
            return agent

        mock_ca.side_effect = make_agent

        case = TestCase(goal="test", runs=1, threshold=0.99)
        result = run_test_case(case, sample_config, Path("/tmp/test"), None)
        # Single run: binary pass/fail, no CI fields
        assert result.ci_lower is None
        assert result.ci_upper is None
        assert result.passed


# --- Group E: CLI integration ---


class TestCLI:
    def test_test_command_help(self):
        from typer.testing import CliRunner

        from blueclaw.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["test", "--help"])
        assert result.exit_code == 0
        assert "SPEC_PATH" in result.output

    def test_test_dry_run(self, sample_test_spec):
        from typer.testing import CliRunner

        from blueclaw.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["test", str(sample_test_spec), "--dry-run"])
        assert result.exit_code == 0
        assert "Spec valid" in result.output or "Warning" in result.output

    @patch("blueclaw.testing.run_spec")
    @patch("blueclaw.testing.build_model")
    def test_exit_code_all_pass(self, mock_bm, mock_rs, sample_test_spec):
        from typer.testing import CliRunner

        from blueclaw.cli import app

        mock_rs.return_value = [TestResult(goal="test", passed=True, verdict="pass")]
        runner = CliRunner()
        result = runner.invoke(app, ["test", str(sample_test_spec)])
        assert result.exit_code == 0

    @patch("blueclaw.testing.run_spec")
    @patch("blueclaw.testing.build_model")
    def test_exit_code_any_fail(self, mock_bm, mock_rs, sample_test_spec):
        from typer.testing import CliRunner

        from blueclaw.cli import app

        mock_rs.return_value = [
            TestResult(
                goal="test",
                passed=False,
                verdict="fail",
                failures=["bad"],
            )
        ]
        runner = CliRunner()
        result = runner.invoke(app, ["test", str(sample_test_spec)])
        assert result.exit_code == 1

    @patch("blueclaw.testing.run_spec")
    @patch("blueclaw.testing.build_model")
    def test_exit_code_inconclusive_only(self, mock_bm, mock_rs, sample_test_spec):
        from typer.testing import CliRunner

        from blueclaw.cli import app

        mock_rs.return_value = [
            TestResult(
                goal="test",
                passed=False,
                verdict="inconclusive",
                pass_count=24,
                total_runs=30,
            )
        ]
        runner = CliRunner()
        result = runner.invoke(app, ["test", str(sample_test_spec)])
        assert result.exit_code == 0

    def test_trace_replay_stub_tools_flag(self):
        from typer.testing import CliRunner

        from blueclaw.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["trace", "replay", "--help"])
        assert result.exit_code == 0
        assert "--stub-tools" in result.output
