"""Tests for CLI commands."""

import json
import tempfile
from pathlib import Path

import pytest
from click.testing import CliRunner

import reviewmymcp.cli as cli_module
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
    assert "dimension_scores" in parsed
    assert "tool_count" in parsed


def test_replay_json_reports_expected_fixture_findings(sample_stdio_log):
    runner = CliRunner()
    result = runner.invoke(cli, ["replay", str(sample_stdio_log), "--output", "json", "--no-llm-judges"])
    assert result.exit_code == 1

    parsed = json.loads(result.output)
    finding_ids = {f["check_id"] for f in parsed["top_findings"]}

    assert parsed["call_count"] == 4
    assert "accuracy.schema-misuse" in finding_ids
    assert "composability.error-recovery-surface" in finding_ids
    assert any(f["severity"] == "high" for f in parsed["top_findings"])


def test_replay_persistence_path_option_flags_unsafe_default(sample_stdio_log):
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "replay",
            str(sample_stdio_log),
            "--output",
            "json",
            "--dimensions",
            "reliability",
            "--no-llm-judges",
            "--persistence-path",
            "MEMORY_FILE_PATH=/tmp/memory.jsonl",
        ],
    )
    assert result.exit_code in (0, 1)
    parsed = json.loads(result.output)
    finding_ids = [f["check_id"] for f in parsed["top_findings"]]
    assert "reliability.unsafe-persistence-default" in finding_ids


def test_runtime_metadata_pins_unversioned_npx_package(monkeypatch):
    monkeypatch.setattr(cli_module, "_npm_view_version", lambda package_name: "1.1.0")
    monkeypatch.setattr(cli_module, "_sha256_file", lambda path: "abc123")
    monkeypatch.setattr(cli_module.shutil, "which", lambda executable: f"/usr/bin/{executable}")

    meta = cli_module._resolve_runtime_metadata(["npx", "-y", "ddg-mcp-search"])

    assert meta.package_manager == "npm"
    assert meta.package_name == "ddg-mcp-search"
    assert meta.package_version == "1.1.0"
    assert meta.reproducible_command == "npx -y ddg-mcp-search@1.1.0"
    assert meta.executable_sha256 == "abc123"


def test_runtime_metadata_resolves_npm_dist_tag(monkeypatch):
    seen_specs = []

    def fake_npm_view_version(package_spec):
        seen_specs.append(package_spec)
        return "1.2.3"

    monkeypatch.setattr(cli_module, "_npm_view_version", fake_npm_view_version)
    monkeypatch.setattr(cli_module, "_sha256_file", lambda path: "")
    monkeypatch.setattr(cli_module.shutil, "which", lambda executable: None)

    meta = cli_module._resolve_runtime_metadata(["npx", "-y", "ddg-mcp-search@latest"])

    assert seen_specs == ["ddg-mcp-search@latest"]
    assert meta.package_name == "ddg-mcp-search"
    assert meta.package_version == "1.2.3"
    assert meta.reproducible_command == "npx -y ddg-mcp-search@1.2.3"


def test_runtime_metadata_handles_scoped_pinned_npm_package():
    name, version = cli_module._split_npm_package_spec("@scope/server@2.0.0")
    assert name == "@scope/server"
    assert version == "2.0.0"


def test_npm_provenance_metadata_from_registry(monkeypatch):
    monkeypatch.setattr(cli_module, "_url_reachable", lambda url: False)
    monkeypatch.setattr(
        cli_module,
        "_npm_view_package_json",
        lambda package_name: {
            "repository": {"url": "git+https://github.com/example/deleted.git"},
            "license": "MIT",
            "time": {
                "created": "2026-01-01T00:00:00.000Z",
                "1.0.0": "2026-01-01T00:01:00.000Z",
                "1.0.1": "2026-01-01T00:20:00.000Z",
            },
        },
    )

    provenance = cli_module._resolve_npm_provenance("example-package")

    assert provenance.repository_url == "https://github.com/example/deleted"
    assert provenance.source_reachable is False
    assert provenance.license_declared == "MIT"
    assert len(provenance.version_publish_times) == 2


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
    assert "dimension_scores" in parsed

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
    assert "accuracy" in result.output

    Path(path1).unlink()
    Path(path2).unlink()


def test_audit_no_target():
    runner = CliRunner()
    result = runner.invoke(cli, ["audit"])
    assert result.exit_code == 2


def test_replay_exits_1_with_critical_findings(sample_stdio_log):
    """With --no-redact, the sample fixture exposes SSN and connection strings -> CRITICAL findings -> exit 1."""
    runner = CliRunner()
    result = runner.invoke(cli, ["replay", str(sample_stdio_log), "--no-redact", "--no-llm-judges"])
    assert result.exit_code == 1


def test_audit_exits_1_with_critical_findings(sample_stdio_log):
    """audit --log-file with --no-redact triggers CRITICAL security findings -> exit 1."""
    runner = CliRunner()
    result = runner.invoke(cli, ["audit", "--log-file", str(sample_stdio_log), "--no-redact", "--no-llm-judges"])
    assert result.exit_code == 1


def test_audit_exits_0_without_high_or_critical_findings():
    events = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 1, "result": {"serverInfo": {"name": "S", "version": "1.0"}}},
    ]
    with tempfile.NamedTemporaryFile(mode="w", suffix=".ndjson", delete=False) as f:
        path = f.name
        for event in events:
            f.write(json.dumps(event) + "\n")

    runner = CliRunner()
    result = runner.invoke(cli, ["audit", "--log-file", path, "--no-llm-judges"])
    assert result.exit_code == 0

    Path(path).unlink()


@pytest.fixture
def sample_stdio_log():
    return Path(__file__).parent / "fixtures" / "sample_stdio_log.ndjson"
