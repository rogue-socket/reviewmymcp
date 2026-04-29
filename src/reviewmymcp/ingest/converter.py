"""Log format converters — transform foreign MCP log formats to canonical NDJSON."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class LogConverter(Protocol):
    """Protocol for log format converters."""

    format_name: str

    def convert(self, input_path: Path) -> list[dict[str, Any]]:
        """Convert a foreign log file to a list of canonical NDJSON wrapper dicts."""
        ...


class ConverterRegistry:
    """Registry of available log format converters."""

    def __init__(self) -> None:
        self._converters: dict[str, LogConverter] = {}

    def register(self, converter: LogConverter) -> None:
        self._converters[converter.format_name] = converter

    def get(self, format_name: str) -> LogConverter | None:
        return self._converters.get(format_name)

    def list_formats(self) -> list[str]:
        return sorted(self._converters.keys())


class PassthroughConverter:
    """Passes through logs already in canonical NDJSON or JSON array format."""

    format_name: str = "passthrough"

    def convert(self, input_path: Path) -> list[dict[str, Any]]:
        text = input_path.read_text(encoding="utf-8")
        stripped = text.strip()

        # JSON array
        if stripped.startswith("["):
            records = json.loads(stripped)
            if not isinstance(records, list):
                raise ValueError(f"Expected JSON array, got {type(records).__name__}")
            return records

        # NDJSON
        records = []
        for i, line in enumerate(text.splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid JSON on line {i}: {e}") from e
            if not isinstance(obj, dict):
                raise ValueError(f"Expected object on line {i}, got {type(obj).__name__}")
            records.append(obj)
        return records


class ClaudeDesktopConverter:
    """Converter for Claude Desktop MCP log format (stub)."""

    format_name: str = "claude-desktop"

    def convert(self, input_path: Path) -> list[dict[str, Any]]:
        raise NotImplementedError(
            "Claude Desktop does not log wire-level JSON-RPC traffic. "
            "Its logs (~/Library/Logs/Claude/mcp.log) contain only operational messages. "
            "To capture MCP traffic from Claude Desktop, run:\n"
            "  reviewmymcp watch \"your-server-command\" --output-dir ./logs\n"
            "then configure Claude Desktop to connect to the proxy."
        )


class PythonSdkConverter:
    """Converter for Python MCP SDK debug log format (stub)."""

    format_name: str = "python-sdk"

    def convert(self, input_path: Path) -> list[dict[str, Any]]:
        raise NotImplementedError(
            "Python MCP SDK log format converter is not yet implemented. "
            "The Python SDK outputs debug logs to stderr with JSON-RPC payloads. "
            "For now, extract the JSON-RPC messages and save as NDJSON."
        )


def build_registry() -> ConverterRegistry:
    """Build the default converter registry with all known converters."""
    registry = ConverterRegistry()
    registry.register(PassthroughConverter())
    registry.register(ClaudeDesktopConverter())
    registry.register(PythonSdkConverter())
    return registry
