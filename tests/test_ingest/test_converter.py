"""Tests for log format converters."""

import json
import tempfile
from pathlib import Path

import pytest
from click.testing import CliRunner

from reviewmymcp.cli import cli
from reviewmymcp.ingest.converter import (
    ClaudeDesktopConverter,
    ConverterRegistry,
    PassthroughConverter,
    PythonSdkConverter,
    build_registry,
)

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


def test_passthrough_ndjson():
    converter = PassthroughConverter()
    records = converter.convert(FIXTURES_DIR / "sample_stdio_log.ndjson")
    assert len(records) > 0
    assert all(isinstance(r, dict) for r in records)
    # First record should be an initialize request
    assert records[0]["method"] == "initialize"


def test_passthrough_json_array():
    records_in = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}]
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(records_in, f)
        path = f.name

    try:
        converter = PassthroughConverter()
        records = converter.convert(Path(path))
        assert len(records) == 1
        assert records[0]["method"] == "initialize"
    finally:
        Path(path).unlink()


def test_passthrough_invalid_json():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".ndjson", delete=False) as f:
        f.write("not valid json\n")
        path = f.name

    try:
        converter = PassthroughConverter()
        with pytest.raises(ValueError, match="Invalid JSON on line 1"):
            converter.convert(Path(path))
    finally:
        Path(path).unlink()


def test_registry_register_and_get():
    registry = ConverterRegistry()
    converter = PassthroughConverter()
    registry.register(converter)
    assert registry.get("passthrough") is converter
    assert registry.get("nonexistent") is None


def test_registry_list_formats():
    registry = build_registry()
    formats = registry.list_formats()
    assert "passthrough" in formats
    assert "claude-desktop" in formats
    assert "python-sdk" in formats


def test_claude_desktop_stub():
    converter = ClaudeDesktopConverter()
    with pytest.raises(NotImplementedError, match="Claude Desktop"):
        converter.convert(Path("dummy.json"))


def test_python_sdk_stub():
    converter = PythonSdkConverter()
    with pytest.raises(NotImplementedError, match="Python MCP SDK"):
        converter.convert(Path("dummy.json"))


def test_convert_cli_passthrough():
    runner = CliRunner()
    result = runner.invoke(cli, ["convert", str(FIXTURES_DIR / "sample_stdio_log.ndjson")])
    assert result.exit_code == 0
    lines = [l for l in result.output.strip().split("\n") if l.strip()]
    assert len(lines) > 0
    # Each line should be valid JSON
    for line in lines:
        parsed = json.loads(line)
        assert isinstance(parsed, dict)


def test_convert_cli_to_file():
    with tempfile.NamedTemporaryFile(suffix=".ndjson", delete=False) as f:
        out_path = f.name

    try:
        runner = CliRunner()
        result = runner.invoke(
            cli, ["convert", str(FIXTURES_DIR / "sample_stdio_log.ndjson"), "--output-file", out_path]
        )
        assert result.exit_code == 0
        content = Path(out_path).read_text()
        lines = [l for l in content.strip().split("\n") if l.strip()]
        assert len(lines) > 0
    finally:
        Path(out_path).unlink()


def test_convert_cli_stub_format_errors():
    runner = CliRunner()
    result = runner.invoke(
        cli, ["convert", str(FIXTURES_DIR / "sample_stdio_log.ndjson"), "--format", "claude-desktop"]
    )
    assert result.exit_code == 2
    output = (result.output + (result.stderr or "")).lower()
    assert "does not log" in output or "not yet implemented" in output or "not implemented" in output
