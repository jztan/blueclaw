"""Tests for blueclaw.testing — spec loading, assertions, formatters, runner."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch
from xml.etree import ElementTree as ET

import pytest

from blueclaw.models import TestCase, TestResult, TestSpec


class TestArtifactsRoot:
    def test_artifacts_root_uses_function_param_first(self, tmp_path, monkeypatch):
        from blueclaw.testing import _artifacts_root

        monkeypatch.setenv("BLUECLAW_ARTIFACTS_ROOT", str(tmp_path / "from-env"))
        explicit = tmp_path / "from-param"
        result = _artifacts_root(artifacts_root=explicit)
        assert result is not None
        # The result is the *invocation* dir under the root, not the root itself
        assert result.parent == explicit
        assert result.exists()

    def test_artifacts_root_falls_back_to_env_var(self, tmp_path, monkeypatch):
        from blueclaw.testing import _artifacts_root

        env_root = tmp_path / "from-env"
        monkeypatch.setenv("BLUECLAW_ARTIFACTS_ROOT", str(env_root))
        result = _artifacts_root(artifacts_root=None)
        assert result is not None
        assert result.parent == env_root
        assert result.exists()

    def test_artifacts_root_default_when_unset(self, tmp_path, monkeypatch):
        from blueclaw.testing import _artifacts_root

        monkeypatch.delenv("BLUECLAW_ARTIFACTS_ROOT", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))  # redirect ~
        result = _artifacts_root(artifacts_root=None)
        assert result is not None
        # Default is ~/blueclaw/test-runs/<invocation-ts>/
        assert "blueclaw" in str(result) and "test-runs" in str(result)
        assert result.exists()

    def test_artifacts_root_timestamp_format(self, tmp_path):
        from blueclaw.testing import _artifacts_root
        import re

        result = _artifacts_root(artifacts_root=tmp_path)
        # Format: YYYYMMDDTHHMMSSfffZ-<4hex>
        name = result.name
        assert re.match(r"^\d{8}T\d{6}\d{3}Z-[0-9a-f]{4}$", name), name

    def test_artifacts_root_two_calls_produce_different_paths(self, tmp_path):
        from blueclaw.testing import _artifacts_root

        a = _artifacts_root(artifacts_root=tmp_path)
        b = _artifacts_root(artifacts_root=tmp_path)
        assert a != b  # 4-hex suffix disarms ms-collisions

    def test_artifacts_root_returns_none_on_mkdir_failure(
        self, tmp_path, monkeypatch, capsys
    ):
        from blueclaw.testing import _artifacts_root

        # Point at a path under a non-directory file — mkdir will fail
        not_a_dir = tmp_path / "blocker"
        not_a_dir.write_text("not a directory")
        result = _artifacts_root(artifacts_root=not_a_dir / "below")
        assert result is None
        captured = capsys.readouterr()
        assert "artifact capture disabled" in captured.err.lower()


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


class TestTestResultModel:
    def test_test_result_has_artifacts_path_field(self):
        from blueclaw.models import TestResult

        # Default is None (back-compat with existing call sites)
        r = TestResult(goal="x", passed=True)
        assert r.artifacts_path is None

        # Can be set to a string path
        r2 = TestResult(goal="x", passed=False, artifacts_path="/tmp/foo/run-000")
        assert r2.artifacts_path == "/tmp/foo/run-000"


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

    def test_validate_spec_contradictory_tools(self):
        from blueclaw.testing import validate_spec

        spec = TestSpec(
            tests=[
                TestCase(
                    goal="test",
                    expected_tools=["shell_command"],
                    forbidden_tools=["shell_command"],
                )
            ]
        )
        warnings = validate_spec(spec)
        assert any("both expected and forbidden" in w for w in warnings)

    def test_validate_spec_invalid_regex(self):
        from blueclaw.testing import validate_spec

        spec = TestSpec(tests=[TestCase(goal="test", output_regex="[invalid")])
        warnings = validate_spec(spec)
        assert any("invalid output_regex" in w for w in warnings)

    def test_validate_spec_invalid_forbidden_regex(self):
        from blueclaw.testing import validate_spec

        spec = TestSpec(
            tests=[TestCase(goal="test", forbidden_output_regex="[invalid")]
        )
        warnings = validate_spec(spec)
        assert any("invalid forbidden_output_regex" in w for w in warnings)

    def test_validate_spec_negative_duration(self):
        from blueclaw.testing import validate_spec

        spec = TestSpec(tests=[TestCase(goal="test", max_duration_s=-5.0)])
        warnings = validate_spec(spec)
        assert any("max_duration_s must be > 0" in w for w in warnings)


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

    # --- forbidden_tools ---

    def test_forbidden_tools_pass(self):
        from blueclaw.testing import _check_assertions

        case = TestCase(goal="test", forbidden_tools=["http_request"])
        failures = _check_assertions(case, ["web_search"], "ok", 1, 0.01)
        assert failures == []

    def test_forbidden_tools_fail(self):
        from blueclaw.testing import _check_assertions

        case = TestCase(goal="test", forbidden_tools=["http_request"])
        failures = _check_assertions(
            case, ["web_search", "http_request"], "ok", 1, 0.01
        )
        assert any("Forbidden tools called: http_request" in f for f in failures)

    # --- expected_files ---

    def test_expected_files_pass(self, tmp_path):
        from blueclaw.testing import _check_assertions

        (tmp_path / "hello.txt").write_text("hi")
        case = TestCase(goal="test", expected_files=["hello.txt"])
        failures = _check_assertions(case, [], "ok", 1, 0.01, workspace_root=tmp_path)
        assert failures == []

    def test_expected_files_fail(self, tmp_path):
        from blueclaw.testing import _check_assertions

        case = TestCase(goal="test", expected_files=["missing.txt"])
        failures = _check_assertions(case, [], "ok", 1, 0.01, workspace_root=tmp_path)
        assert any("expected_files: file not found: missing.txt" in f for f in failures)

    def test_expected_files_no_workspace(self):
        from blueclaw.testing import _check_assertions

        case = TestCase(goal="test", expected_files=["hello.txt"])
        failures = _check_assertions(case, [], "ok", 1, 0.01, workspace_root=None)
        assert any("expected_files requires workspace context" in f for f in failures)

    def test_expected_files_path_traversal(self, tmp_path):
        from blueclaw.testing import _check_assertions

        case = TestCase(goal="test", expected_files=["../../etc/passwd"])
        failures = _check_assertions(case, [], "ok", 1, 0.01, workspace_root=tmp_path)
        assert any("path outside workspace" in f for f in failures)

    def test_expected_files_dir_prefix_attack(self, tmp_path):
        from blueclaw.testing import _check_assertions

        # Create sibling dir with shared prefix
        sibling = tmp_path.parent / (tmp_path.name + "bar")
        sibling.mkdir(exist_ok=True)
        (sibling / "secret.txt").write_text("secret")
        try:
            # Relative path that resolves to sibling
            rel = f"../{sibling.name}/secret.txt"
            case = TestCase(goal="test", expected_files=[rel])
            failures = _check_assertions(
                case, [], "ok", 1, 0.01, workspace_root=tmp_path
            )
            assert any("path outside workspace" in f for f in failures)
        finally:
            (sibling / "secret.txt").unlink(missing_ok=True)
            sibling.rmdir()

    # --- expected_file_contains ---

    def test_expected_file_contains_pass(self, tmp_path):
        from blueclaw.testing import _check_assertions

        (tmp_path / "notes.txt").write_text("Hello World")
        case = TestCase(goal="test", expected_file_contains={"notes.txt": "hello"})
        failures = _check_assertions(case, [], "ok", 1, 0.01, workspace_root=tmp_path)
        assert failures == []

    def test_expected_file_contains_fail_missing(self, tmp_path):
        from blueclaw.testing import _check_assertions

        case = TestCase(goal="test", expected_file_contains={"missing.txt": "hello"})
        failures = _check_assertions(case, [], "ok", 1, 0.01, workspace_root=tmp_path)
        assert any(
            "expected_file_contains: file not found: missing.txt" in f for f in failures
        )

    def test_expected_file_contains_fail_content(self, tmp_path):
        from blueclaw.testing import _check_assertions

        (tmp_path / "notes.txt").write_text("something else")
        case = TestCase(goal="test", expected_file_contains={"notes.txt": "hello"})
        failures = _check_assertions(case, [], "ok", 1, 0.01, workspace_root=tmp_path)
        assert any("does not contain: 'hello'" in f for f in failures)

    def test_expected_file_contains_no_workspace(self):
        from blueclaw.testing import _check_assertions

        case = TestCase(goal="test", expected_file_contains={"notes.txt": "hello"})
        failures = _check_assertions(case, [], "ok", 1, 0.01, workspace_root=None)
        assert any(
            "expected_file_contains requires workspace context" in f for f in failures
        )

    def test_expected_file_contains_path_traversal(self, tmp_path):
        from blueclaw.testing import _check_assertions

        case = TestCase(
            goal="test",
            expected_file_contains={"../../etc/passwd": "root"},
        )
        failures = _check_assertions(case, [], "ok", 1, 0.01, workspace_root=tmp_path)
        assert any("path outside workspace" in f for f in failures)

    def test_expected_file_contains_case_insensitive(self, tmp_path):
        from blueclaw.testing import _check_assertions

        (tmp_path / "notes.txt").write_text("HELLO WORLD")
        case = TestCase(
            goal="test", expected_file_contains={"notes.txt": "hello world"}
        )
        failures = _check_assertions(case, [], "ok", 1, 0.01, workspace_root=tmp_path)
        assert failures == []

    def test_expected_file_contains_unreadable(self, tmp_path):
        from blueclaw.testing import _check_assertions

        # A directory is not readable as text
        subdir = tmp_path / "adir"
        subdir.mkdir()
        case = TestCase(goal="test", expected_file_contains={"adir": "hello"})
        failures = _check_assertions(case, [], "ok", 1, 0.01, workspace_root=tmp_path)
        assert any("expected_file_contains: cannot read" in f for f in failures)

    # --- forbidden_output_contains ---

    def test_forbidden_output_pass(self):
        from blueclaw.testing import _check_assertions

        case = TestCase(goal="test", forbidden_output_contains="error")
        failures = _check_assertions(case, [], "all good", 1, 0.01)
        assert failures == []

    def test_forbidden_output_fail(self):
        from blueclaw.testing import _check_assertions

        case = TestCase(goal="test", forbidden_output_contains="error")
        failures = _check_assertions(case, [], "An ERROR occurred", 1, 0.01)
        assert any("Output contains forbidden text" in f for f in failures)

    # --- output_regex ---

    def test_output_regex_pass(self):
        from blueclaw.testing import _check_assertions

        case = TestCase(goal="test", output_regex=r"\d+ results")
        failures = _check_assertions(case, [], "Found 42 results", 1, 0.01)
        assert failures == []

    def test_output_regex_fail(self):
        from blueclaw.testing import _check_assertions

        case = TestCase(goal="test", output_regex=r"\d+ results")
        failures = _check_assertions(case, [], "no matches", 1, 0.01)
        assert any("Output does not match regex" in f for f in failures)

    def test_output_regex_invalid(self):
        from blueclaw.testing import _check_assertions

        case = TestCase(goal="test", output_regex=r"[invalid")
        failures = _check_assertions(case, [], "text", 1, 0.01)
        assert any("Invalid regex" in f for f in failures)

    def test_output_regex_case_sensitive(self):
        from blueclaw.testing import _check_assertions

        case = TestCase(goal="test", output_regex=r"Hello")
        failures = _check_assertions(case, [], "hello world", 1, 0.01)
        assert any("Output does not match regex" in f for f in failures)

    # --- forbidden_output_regex ---

    def test_forbidden_output_regex_pass(self):
        from blueclaw.testing import _check_assertions

        case = TestCase(goal="test", forbidden_output_regex=r"(?i)error \d+")
        failures = _check_assertions(case, [], "everything worked fine", 1, 0.01)
        assert failures == []

    def test_forbidden_output_regex_fail(self):
        from blueclaw.testing import _check_assertions

        case = TestCase(goal="test", forbidden_output_regex=r"(?i)error \d+")
        failures = _check_assertions(case, [], "got Error 42", 1, 0.01)
        assert any("Output matches forbidden regex" in f for f in failures)

    def test_forbidden_output_regex_invalid(self):
        from blueclaw.testing import _check_assertions

        case = TestCase(goal="test", forbidden_output_regex=r"[invalid")
        failures = _check_assertions(case, [], "text", 1, 0.01)
        assert any("Invalid forbidden regex" in f for f in failures)

    # --- tool_order ---

    def test_tool_order_pass(self):
        from blueclaw.testing import _check_assertions

        case = TestCase(goal="test", tool_order=["web_search", "shell_command"])
        failures = _check_assertions(
            case, ["web_search", "shell_command"], "ok", 2, 0.01
        )
        assert failures == []

    def test_tool_order_fail(self):
        from blueclaw.testing import _check_assertions

        case = TestCase(goal="test", tool_order=["shell_command", "web_search"])
        failures = _check_assertions(
            case, ["web_search", "shell_command"], "ok", 2, 0.01
        )
        assert any("Tool order violation" in f for f in failures)

    def test_tool_order_with_extras(self):
        from blueclaw.testing import _check_assertions

        case = TestCase(goal="test", tool_order=["web_search", "shell_command"])
        failures = _check_assertions(
            case,
            ["web_search", "http_request", "shell_command"],
            "ok",
            3,
            0.01,
        )
        assert failures == []

    def test_tool_order_empty(self):
        from blueclaw.testing import _check_assertions

        case = TestCase(goal="test", tool_order=[])
        failures = _check_assertions(case, ["web_search"], "ok", 1, 0.01)
        assert not any("Tool order" in f for f in failures)

    # --- max_duration_s ---

    def test_max_duration_pass(self):
        from blueclaw.testing import _check_assertions

        case = TestCase(goal="test", max_duration_s=10.0)
        failures = _check_assertions(case, [], "ok", 1, 0.01, duration_s=5.0)
        assert failures == []

    def test_max_duration_fail(self):
        from blueclaw.testing import _check_assertions

        case = TestCase(goal="test", max_duration_s=10.0)
        failures = _check_assertions(case, [], "ok", 1, 0.01, duration_s=15.0)
        assert any("Duration exceeded" in f for f in failures)


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

    def test_format_tap_inconclusive_with_failures(self):
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
                failures=["Missing tools: fetch_url", "Cost exceeded: $0.05"],
            )
        ]
        output = format_tap(results)
        assert "INCONCLUSIVE" in output
        assert "failures:" in output
        assert '"Missing tools: fetch_url"' in output
        assert '"Cost exceeded: $0.05"' in output


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

    def test_format_junit_inconclusive_with_failures(self):
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
                failures=["Missing tools: fetch_url", "Cost exceeded: $0.05"],
            )
        ]
        output = format_junit(results)
        root = ET.fromstring(output)
        skipped = root.find(".//skipped")
        assert skipped is not None
        msg = skipped.attrib["message"]
        assert "INCONCLUSIVE" in msg
        assert "Missing tools: fetch_url" in msg
        assert "Cost exceeded: $0.05" in msg

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
    def test_single_run_writes_result_json(self, mock_bm, mock_ca, sample_config):
        from blueclaw.testing import _run_single

        mock_agent = MagicMock()
        mock_agent.return_value = _make_mock_agent_result()
        mock_ca.return_value = mock_agent

        case = TestCase(
            goal="create hello.txt",
            expected_output_contains="hello.txt",
        )
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            (ws / ".blueclaw").mkdir(parents=True)
            result = _run_single(case, sample_config, ws, None)
            result_file = ws / ".blueclaw" / "result.json"
            assert result_file.exists()
            data = json.loads(result_file.read_text())
            assert data["goal"] == "create hello.txt"
            assert data["verdict"] == result.verdict

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
    def test_multi_run_writes_per_run_results(self, mock_ca, sample_config):
        """Each run-NNN dir gets .blueclaw/result.json."""
        from blueclaw.testing import run_test_case

        def make_agent(*args, **kwargs):
            agent = MagicMock()
            agent.return_value = _make_mock_agent_result()
            return agent

        mock_ca.side_effect = make_agent

        import tempfile

        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            case = TestCase(goal="test", runs=3)
            run_test_case(case, sample_config, ws, None)
            for i in range(3):
                result_file = ws / f"run-{i:03d}" / ".blueclaw" / "result.json"
                assert result_file.exists(), f"run-{i:03d}/result.json missing"

    @patch("blueclaw.testing.create_agent")
    def test_result_json_contains_failures(self, mock_ca, sample_config):
        """Failed assertion shows up in result.json."""
        from blueclaw.testing import run_test_case

        def make_agent(*args, **kwargs):
            agent = MagicMock()
            result = _make_mock_agent_result()
            result.message = {"content": [{"text": "no match"}]}
            agent.return_value = result
            return agent

        mock_ca.side_effect = make_agent

        import tempfile

        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            case = TestCase(
                goal="test",
                expected_output_contains="hello",
                runs=1,
            )
            run_test_case(case, sample_config, ws, None)
            result_file = ws / ".blueclaw" / "result.json"
            data = json.loads(result_file.read_text())
            assert data["verdict"] == "fail"
            assert any("hello" in f for f in data["failures"])

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
        import re

        from typer.testing import CliRunner

        from blueclaw.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["trace", "replay", "--help"])
        assert result.exit_code == 0
        plain = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
        assert "--stub-tools" in plain


class TestWriteArtifacts:
    def test_write_artifacts_happy_path(self, tmp_path):
        from blueclaw.testing import _write_artifacts

        failures = _write_artifacts(
            invocation_dir=tmp_path,
            case_idx=0,
            run_idx=0,
            response_text="The answer is 4.",
            messages=[{"role": "user", "content": [{"text": "hi"}]}],
        )
        assert failures == []
        run_dir = tmp_path / "case-000" / "run-000"
        assert (run_dir / "response.txt").read_text() == "The answer is 4."
        import json

        msgs = json.loads((run_dir / "messages.json").read_text())
        assert msgs == [{"role": "user", "content": [{"text": "hi"}]}]

    def test_write_artifacts_empty_response_writes_empty_file(self, tmp_path):
        from blueclaw.testing import _write_artifacts

        failures = _write_artifacts(
            invocation_dir=tmp_path,
            case_idx=0,
            run_idx=0,
            response_text="",
            messages=[],
        )
        assert failures == []
        assert (tmp_path / "case-000" / "run-000" / "response.txt").read_text() == ""

    def test_write_artifacts_mkdir_failure(self, tmp_path):
        from blueclaw.testing import _write_artifacts

        # Block the run subdir by writing a non-directory file at its location
        blocker = tmp_path / "case-000"
        blocker.write_text("blocker")
        failures = _write_artifacts(
            invocation_dir=tmp_path,
            case_idx=0,
            run_idx=0,
            response_text="x",
            messages=[],
        )
        assert len(failures) == 1
        assert failures[0]["stage"] == "mkdir"
        assert failures[0]["case_idx"] == 0
        assert failures[0]["run_idx"] == 0
        assert "reason" in failures[0]

    def test_write_artifacts_response_failure_does_not_block_messages(
        self, tmp_path, monkeypatch
    ):
        from blueclaw.testing import _write_artifacts
        from pathlib import Path

        # Stub Path.write_text to raise only for response.txt
        orig = Path.write_text

        def fake_write(self, content, *args, **kwargs):
            if self.name == "response.txt":
                raise OSError("simulated response.txt failure")
            return orig(self, content, *args, **kwargs)

        monkeypatch.setattr(Path, "write_text", fake_write)

        failures = _write_artifacts(
            invocation_dir=tmp_path,
            case_idx=0,
            run_idx=0,
            response_text="x",
            messages=[{"role": "user"}],
        )
        # Response failed, messages succeeded
        assert len(failures) == 1
        assert failures[0]["stage"] == "response.txt"
        assert (tmp_path / "case-000" / "run-000" / "messages.json").exists()

    def test_write_artifacts_messages_failure_does_not_block_response(
        self, tmp_path, monkeypatch
    ):
        from blueclaw.testing import _write_artifacts
        from pathlib import Path

        orig = Path.write_text

        def fake_write(self, content, *args, **kwargs):
            if self.name == "messages.json":
                raise OSError("simulated messages.json failure")
            return orig(self, content, *args, **kwargs)

        monkeypatch.setattr(Path, "write_text", fake_write)

        failures = _write_artifacts(
            invocation_dir=tmp_path,
            case_idx=0,
            run_idx=0,
            response_text="x",
            messages=[],
        )
        assert len(failures) == 1
        assert failures[0]["stage"] == "messages.json"
        assert (tmp_path / "case-000" / "run-000" / "response.txt").exists()


class TestRunSingleCapture:
    def _make_stub_agent_factory(self, response_text="hi", raise_exc=None):
        """Return a function that monkeypatches create_agent with a stub."""
        from unittest.mock import MagicMock

        def patch(monkeypatch):
            stub_agent = MagicMock()
            stub_agent.messages = [
                {"role": "user", "content": [{"text": "x"}]},
                {"role": "assistant", "content": [{"text": response_text}]},
            ]
            if raise_exc is not None:
                stub_agent.side_effect = raise_exc
            else:
                fake_result = MagicMock()
                fake_result.message = {"content": [{"text": response_text}]}
                fake_result.metrics.accumulated_usage = {
                    "inputTokens": 0,
                    "outputTokens": 0,
                }
                stub_agent.return_value = fake_result
            monkeypatch.setattr(
                "blueclaw.testing.create_agent", lambda *a, **kw: stub_agent
            )
            monkeypatch.setattr(
                "blueclaw.testing.cleanup_mcp_clients", lambda *a, **kw: None
            )

        return patch

    def test_run_single_writes_artifacts_on_success(self, tmp_path, monkeypatch):
        from blueclaw.testing import _run_single
        from blueclaw.models import TestCase, SessionConfig

        self._make_stub_agent_factory(response_text="the answer is 4")(monkeypatch)
        config = SessionConfig(provider="anthropic", model_id="claude-haiku-4-5")
        capture_failures: list[dict] = []
        result = _run_single(
            TestCase(goal="test"),
            config,
            tmp_path / "ws",
            model=None,
            invocation_dir=tmp_path / "artifacts",
            case_idx=0,
            run_idx=0,
            capture_failures=capture_failures,
        )
        run_dir = tmp_path / "artifacts" / "case-000" / "run-000"
        assert (run_dir / "response.txt").read_text() == "the answer is 4"
        assert (run_dir / "messages.json").exists()
        assert result.artifacts_path == str(run_dir)
        assert capture_failures == []

    def test_run_single_writes_partial_artifacts_on_exception(
        self, tmp_path, monkeypatch
    ):
        from blueclaw.testing import _run_single
        from blueclaw.models import TestCase, SessionConfig

        self._make_stub_agent_factory(raise_exc=RuntimeError("kaboom"))(monkeypatch)
        config = SessionConfig(provider="anthropic", model_id="claude-haiku-4-5")
        capture_failures: list[dict] = []
        result = _run_single(
            TestCase(goal="test"),
            config,
            tmp_path / "ws",
            model=None,
            invocation_dir=tmp_path / "artifacts",
            case_idx=0,
            run_idx=0,
            capture_failures=capture_failures,
        )
        run_dir = tmp_path / "artifacts" / "case-000" / "run-000"
        # response.txt exists (empty — no successful result)
        assert (run_dir / "response.txt").read_text() == ""
        # messages.json exists with whatever stub_agent.messages was
        assert (run_dir / "messages.json").exists()
        assert result.error == "kaboom"

    def test_run_single_handles_result_message_none(self, tmp_path, monkeypatch):
        from blueclaw.testing import _run_single
        from blueclaw.models import TestCase, SessionConfig
        from unittest.mock import MagicMock

        stub_agent = MagicMock()
        stub_agent.messages = []
        fake_result = MagicMock()
        fake_result.message = None  # the None-safety case
        fake_result.metrics.accumulated_usage = {"inputTokens": 0, "outputTokens": 0}
        stub_agent.return_value = fake_result
        monkeypatch.setattr(
            "blueclaw.testing.create_agent", lambda *a, **kw: stub_agent
        )
        monkeypatch.setattr(
            "blueclaw.testing.cleanup_mcp_clients", lambda *a, **kw: None
        )

        config = SessionConfig(provider="anthropic", model_id="claude-haiku-4-5")
        _run_single(
            TestCase(goal="test"),
            config,
            tmp_path / "ws",
            model=None,
            invocation_dir=tmp_path / "artifacts",
            case_idx=0,
            run_idx=0,
            capture_failures=[],
        )
        # Did not crash; wrote empty response.txt
        run_dir = tmp_path / "artifacts" / "case-000" / "run-000"
        assert (run_dir / "response.txt").read_text() == ""

    def test_run_single_skips_capture_when_invocation_dir_is_none(
        self, tmp_path, monkeypatch
    ):
        from blueclaw.testing import _run_single
        from blueclaw.models import TestCase, SessionConfig

        self._make_stub_agent_factory()(monkeypatch)
        config = SessionConfig(provider="anthropic", model_id="claude-haiku-4-5")
        result = _run_single(
            TestCase(goal="test"),
            config,
            tmp_path / "ws",
            model=None,
            invocation_dir=None,
            case_idx=0,
            run_idx=0,
            capture_failures=[],
        )
        # No artifacts written; artifacts_path is None
        assert result.artifacts_path is None
        assert not (tmp_path / "artifacts").exists()

    def test_run_single_records_create_agent_failure(self, tmp_path, monkeypatch):
        """create_agent raising must produce a failed TestResult, not crash."""
        from blueclaw.testing import _run_single
        from blueclaw.models import TestCase, SessionConfig

        def boom(*a, **kw):
            raise RuntimeError("agent setup failed")

        monkeypatch.setattr("blueclaw.testing.create_agent", boom)
        monkeypatch.setattr(
            "blueclaw.testing.cleanup_mcp_clients", lambda *a, **kw: None
        )

        config = SessionConfig(provider="anthropic", model_id="claude-haiku-4-5")
        # Must not raise
        result = _run_single(
            TestCase(goal="test"),
            config,
            tmp_path / "ws",
            model=None,
            invocation_dir=tmp_path / "artifacts",
            case_idx=0,
            run_idx=0,
            capture_failures=[],
        )
        assert result.error == "agent setup failed"
        assert result.verdict == "fail"
        # Capture still attempted with empty messages list
        run_dir = tmp_path / "artifacts" / "case-000" / "run-000"
        assert (run_dir / "response.txt").read_text() == ""
        assert (run_dir / "messages.json").read_text() == "[]"
