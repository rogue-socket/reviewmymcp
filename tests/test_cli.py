"""Tests for CLI commands."""

import json
import tempfile
from pathlib import Path

from click.testing import CliRunner

from reviewmymcp.cli import cli


def test_version():
    runner = CliRunner()
    result = runner.invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.output


def test_list_checks():
    runner = CliRunner()
    result = runner.invoke(cli, ["list-checks"])
    assert result.exit_code == 0
    assert "efficiency" in result.output
    assert "security" in result.output
    assert "conformance" in result.output


def test_replay_terminal(sample_stdio_log):
    runner = CliRunner()
    result = runner.invoke(cli, ["replay", str(sample_stdio_log), "--no-llm-judges"])
    assert result.exit_code in (0, 1)  # 1 if findings, 0 if none
    assert "Dimension Scores" in result.output


def test_replay_json(sample_stdio_log):
    runner = CliRunner()
    result = runner.invoke(cli, ["replay", str(sample_stdio_log), "--output", "json", "--no-llm-judges"])
    assert result.exit_code in (0, 1)
    parsed = json.loads(result.output)
    assert "overall_grade" in parsed
    assert "dimension_scores" in parsed


def test_replay_sarif(sample_stdio_log):
    runner = CliRunner()
    result = runner.invoke(cli, ["replay", str(sample_stdio_log), "--output", "sarif", "--no-llm-judges"])
    assert result.exit_code in (0, 1)
    parsed = json.loads(result.output)
    assert parsed["version"] == "2.1.0"


def test_replay_html(sample_stdio_log):
    runner = CliRunner()
    result = runner.invoke(cli, ["replay", str(sample_stdio_log), "--output", "html", "--no-llm-judges"])
    assert result.exit_code in (0, 1)
    assert "<html" in result.output


def test_replay_to_file(sample_stdio_log):
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name

    runner = CliRunner()
    result = runner.invoke(cli, ["replay", str(sample_stdio_log), "--output", "json", "--output-file", path, "--no-llm-judges"])
    assert result.exit_code in (0, 1)
    content = Path(path).read_text()
    parsed = json.loads(content)
    assert "overall_grade" in parsed

    Path(path).unlink()


def test_replay_dimension_filter(sample_stdio_log):
    runner = CliRunner()
    result = runner.invoke(cli, ["replay", str(sample_stdio_log), "--output", "json", "--dimensions", "efficiency", "--no-llm-judges"])
    parsed = json.loads(result.output)
    dims = [ds["dimension"] for ds in parsed["dimension_scores"]]
    assert dims == ["efficiency"]


def test_diff_command(sample_stdio_log):
    runner = CliRunner()

    # Generate two identical reports
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f1, tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f2:
        path1, path2 = f1.name, f2.name

    runner.invoke(cli, ["replay", str(sample_stdio_log), "--output", "json", "--output-file", path1, "--no-llm-judges"])
    runner.invoke(cli, ["replay", str(sample_stdio_log), "--output", "json", "--output-file", path2, "--no-llm-judges"])

    result = runner.invoke(cli, ["diff", path1, path2])
    assert result.exit_code == 0  # no regressions
    assert "Baseline" in result.output

    Path(path1).unlink()
    Path(path2).unlink()


def test_audit_no_target():
    runner = CliRunner()
    result = runner.invoke(cli, ["audit"])
    assert result.exit_code == 2


from pathlib import Path as _P
import pytest

@pytest.fixture
def sample_stdio_log():
    return _P(__file__).parent / "fixtures" / "sample_stdio_log.ndjson"
