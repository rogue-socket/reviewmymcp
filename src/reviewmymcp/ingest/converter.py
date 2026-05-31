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
    """Converter for Claude Desktop MCP logs that include embedded JSON-RPC payloads."""

    format_name: str = "claude-desktop"

    def convert(self, input_path: Path) -> list[dict[str, Any]]:
        return _extract_embedded_jsonrpc(input_path)


class PythonSdkConverter:
    """Converter for Python MCP SDK debug logs with embedded JSON-RPC payloads."""

    format_name: str = "python-sdk"

    def convert(self, input_path: Path) -> list[dict[str, Any]]:
        return _extract_embedded_jsonrpc(input_path)


def _extract_embedded_jsonrpc(input_path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    decoder = json.JSONDecoder()
    for line in input_path.read_text(encoding="utf-8").splitlines():
        payload = _first_json_object(line, decoder)
        if not payload:
            continue
        message = payload.get("message") if isinstance(payload.get("message"), dict) else payload
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            continue
        records.append({"direction": _direction_hint(line), "message": message})
    if not records:
        raise ValueError(
            "No embedded JSON-RPC messages found. Use `reviewmymcp watch` to capture wire-level traffic."
        )
    return records


def _first_json_object(line: str, decoder: json.JSONDecoder) -> dict[str, Any] | None:
    start = line.find("{")
    while start != -1:
        try:
            value, _ = decoder.raw_decode(line[start:])
        except json.JSONDecodeError:
            start = line.find("{", start + 1)
            continue
        return value if isinstance(value, dict) else None
    return None


def _direction_hint(line: str) -> str:
    lowered = line.lower()
    if any(token in lowered for token in ("server -> client", "to client", "sending response", "response")):
        return "server_to_client"
    return "client_to_server"


def build_registry() -> ConverterRegistry:
    """Build the default converter registry with all known converters."""
    registry = ConverterRegistry()
    registry.register(PassthroughConverter())
    registry.register(ClaudeDesktopConverter())
    registry.register(PythonSdkConverter())
    return registry
