"""Deterministic edge case probes — no LLM needed."""

from __future__ import annotations

from typing import Any

from reviewmymcp.active.safety import WRITE_PREFIXES
from reviewmymcp.ingest.schema import ToolDefinition

CONCURRENT_WRITE_BURST_SIZE = 5
SSRF_TARGETS = (
    ("localhost", "http://127.0.0.1:1/"),
    ("cloud_metadata", "http://169.254.169.254/latest/meta-data/"),
    ("file_scheme", "file:///etc/hosts"),
    ("private_network", "http://10.0.0.1/"),
)
NETWORK_FETCH_NAME_RE = ("fetch", "get_url", "crawl", "http", "request", "browse", "navigate", "scrape", "download")


def generate_edge_probes(tools: list[ToolDefinition]) -> list[dict[str, Any]]:
    """Generate deterministic edge-case tool call requests for each tool."""
    probes: list[dict[str, Any]] = []

    for tool in tools:
        probes.extend(_missing_required_args(tool))
        probes.extend(_wrong_type_args(tool))
        probes.extend(_empty_args(tool))
        probes.extend(_extra_args(tool))

    probes.extend(_nonexistent_tool())
    probes.append(_malformed_jsonrpc())
    probes.append(_missing_method())
    probes.extend(_concurrent_write_integrity(tools))
    probes.extend(_ssrf_url_filtering(tools))

    return probes


def _ssrf_url_filtering(tools: list[ToolDefinition]) -> list[dict[str, Any]]:
    probes: list[dict[str, Any]] = []
    for tool in tools:
        if not _is_network_fetch_tool(tool):
            continue
        url_field = _url_argument_field(tool)
        if url_field is None:
            continue
        for target_name, url in SSRF_TARGETS:
            args = _default_args(tool, 0)
            args[url_field] = url
            probes.append(
                {
                    "probe_type": "ssrf_url_filtering",
                    "description": f"Probe `{tool.name}` URL filtering with {target_name} target",
                    "request": {
                        "jsonrpc": "2.0",
                        "id": None,
                        "method": "tools/call",
                        "params": {"name": tool.name, "arguments": args},
                    },
                }
            )
    return probes


def _is_network_fetch_tool(tool: ToolDefinition) -> bool:
    name = tool.name.lower()
    if any(term in name for term in NETWORK_FETCH_NAME_RE):
        return True
    text = f"{tool.name} {tool.description}".lower()
    return "url" in text and any(term in text for term in ("fetch", "crawl", "browse", "download", "scrape"))


def _url_argument_field(tool: ToolDefinition) -> str | None:
    properties = tool.input_schema.get("properties", {})
    for field in properties:
        normalized = field.lower().replace("_", "")
        if "region" in normalized:
            continue
        if normalized in {"url", "uri", "href", "link", "targeturl", "endpointurl"} or normalized.endswith("url"):
            return field
    return None


def _concurrent_write_integrity(tools: list[ToolDefinition]) -> list[dict[str, Any]]:
    write_tool = next((tool for tool in tools if tool.name.startswith(WRITE_PREFIXES)), None)
    if write_tool is None:
        return []

    group_id = f"concurrent_write_integrity:{write_tool.name}"
    probes = [
        {
            "probe_type": "concurrent_write_integrity",
            "concurrent_group": group_id,
            "description": f"Concurrent write burst {idx + 1}/{CONCURRENT_WRITE_BURST_SIZE} for `{write_tool.name}`",
            "request": {
                "jsonrpc": "2.0",
                "id": None,
                "method": "tools/call",
                "params": {
                    "name": write_tool.name,
                    "arguments": _default_args(write_tool, idx),
                },
            },
        }
        for idx in range(CONCURRENT_WRITE_BURST_SIZE)
    ]

    read_tool = _matching_read_tool(tools)
    if read_tool is not None:
        probes.append(
            {
                "probe_type": "concurrent_write_integrity_readback",
                "description": f"Read back state after concurrent `{write_tool.name}` burst",
                "request": {
                    "jsonrpc": "2.0",
                    "id": None,
                    "method": "tools/call",
                    "params": {
                        "name": read_tool.name,
                        "arguments": _default_args(read_tool, 0),
                    },
                },
            }
        )

    return probes


def _matching_read_tool(tools: list[ToolDefinition]) -> ToolDefinition | None:
    preferred_names = ("read_graph", "read_all", "list", "list_all")
    for name in preferred_names:
        for tool in tools:
            if tool.name == name:
                return tool
    return next(
        (
            tool
            for tool in tools
            if tool.name.startswith(("read_", "list_", "get_", "search_", "open_"))
        ),
        None,
    )


def _missing_required_args(tool: ToolDefinition) -> list[dict[str, Any]]:
    probes = []
    required = tool.input_schema.get("required", [])
    if not required:
        return probes

    probes.append(
        {
            "probe_type": "missing_required_args",
            "description": f"Call `{tool.name}` with no arguments (missing all required: {required})",
            "request": {
                "jsonrpc": "2.0",
                "id": None,
                "method": "tools/call",
                "params": {"name": tool.name, "arguments": {}},
            },
        }
    )

    for field in required:
        partial_args = {
            r: _default_value(tool.input_schema.get("properties", {}).get(r, {})) for r in required if r != field
        }
        probes.append(
            {
                "probe_type": "missing_required_args",
                "description": f"Call `{tool.name}` missing required field `{field}`",
                "request": {
                    "jsonrpc": "2.0",
                    "id": None,
                    "method": "tools/call",
                    "params": {"name": tool.name, "arguments": partial_args},
                },
            }
        )

    return probes


def _wrong_type_args(tool: ToolDefinition) -> list[dict[str, Any]]:
    probes = []
    properties = tool.input_schema.get("properties", {})
    required = tool.input_schema.get("required", [])

    for field, schema in properties.items():
        if field not in required:
            continue
        declared_type = schema.get("type", "string")
        wrong_value = _wrong_type_value(declared_type)
        if wrong_value is None:
            continue

        args = {r: _default_value(properties.get(r, {})) for r in required}
        args[field] = wrong_value

        probes.append(
            {
                "probe_type": "wrong_type_args",
                "description": f"Call `{tool.name}` with wrong type for `{field}` (expected {declared_type})",
                "request": {
                    "jsonrpc": "2.0",
                    "id": None,
                    "method": "tools/call",
                    "params": {"name": tool.name, "arguments": args},
                },
            }
        )

    return probes


def _empty_args(tool: ToolDefinition) -> list[dict[str, Any]]:
    return [
        {
            "probe_type": "empty_args",
            "description": f"Call `{tool.name}` with empty arguments object",
            "request": {
                "jsonrpc": "2.0",
                "id": None,
                "method": "tools/call",
                "params": {"name": tool.name, "arguments": {}},
            },
        }
    ]


def _extra_args(tool: ToolDefinition) -> list[dict[str, Any]]:
    properties = tool.input_schema.get("properties", {})
    required = tool.input_schema.get("required", [])
    args = {r: _default_value(properties.get(r, {})) for r in required}
    args["__unexpected_field__"] = "unexpected_value"

    return [
        {
            "probe_type": "extra_args",
            "description": f"Call `{tool.name}` with an unexpected extra field",
            "request": {
                "jsonrpc": "2.0",
                "id": None,
                "method": "tools/call",
                "params": {"name": tool.name, "arguments": args},
            },
        }
    ]


def _default_args(tool: ToolDefinition, seed: int) -> dict[str, Any]:
    properties = tool.input_schema.get("properties", {})
    required = tool.input_schema.get("required", [])
    selected = required or list(properties)
    return {
        field: _probe_value(properties.get(field, {}), seed, field)
        for field in selected
        if field in properties
    }


def _nonexistent_tool() -> list[dict[str, Any]]:
    return [
        {
            "probe_type": "nonexistent_tool",
            "description": "Call a tool that does not exist",
            "request": {
                "jsonrpc": "2.0",
                "id": None,
                "method": "tools/call",
                "params": {"name": "__nonexistent_tool_12345__", "arguments": {}},
            },
        }
    ]


def _malformed_jsonrpc() -> dict[str, Any]:
    return {
        "probe_type": "malformed_jsonrpc",
        "description": "Send a malformed JSON-RPC message (missing jsonrpc field)",
        "request": {"id": None, "method": "tools/call", "params": {}},
    }


def _missing_method() -> dict[str, Any]:
    return {
        "probe_type": "missing_method",
        "description": "Send a request with no method field",
        "request": {"jsonrpc": "2.0", "id": None},
    }


def _default_value(schema: dict[str, Any]) -> Any:
    t = schema.get("type", "string")
    if t == "string":
        enum = schema.get("enum")
        return enum[0] if enum else "test_value"
    if t == "integer":
        return 1
    if t == "number":
        return 1.0
    if t == "boolean":
        return True
    if t == "array":
        return []
    if t == "object":
        return {}
    return "test_value"


def _probe_value(schema: dict[str, Any], seed: int, field_name: str) -> Any:
    if "enum" in schema and schema["enum"]:
        return schema["enum"][0]

    if "anyOf" in schema:
        candidates = [candidate for candidate in schema["anyOf"] if candidate.get("type") != "null"]
        if candidates:
            return _probe_value(candidates[0], seed, field_name)

    t = schema.get("type", "string")
    if t == "string":
        if "path" in field_name.lower() or "file" in field_name.lower():
            return f"reviewmymcp_probe_{seed}.txt"
        if field_name.lower() in ("content", "body", "message", "text"):
            return f"reviewmymcp concurrent write probe {seed}"
        return f"reviewmymcp_probe_{seed}"
    if t == "integer":
        return seed + 1
    if t == "number":
        return float(seed + 1)
    if t == "boolean":
        return True
    if t == "array":
        return [_probe_value(schema.get("items", {}), seed, field_name.rstrip("s") or "item")]
    if t == "object":
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        selected = required or list(properties)
        return {
            field: _probe_value(properties.get(field, {}), seed, field)
            for field in selected
            if field in properties
        }
    return f"reviewmymcp_probe_{seed}"


def _wrong_type_value(declared_type: str) -> Any:
    if declared_type == "string":
        return 12345
    if declared_type in ("integer", "number"):
        return "not_a_number"
    if declared_type == "boolean":
        return "not_a_boolean"
    if declared_type == "array":
        return "not_an_array"
    if declared_type == "object":
        return "not_an_object"
    return None
