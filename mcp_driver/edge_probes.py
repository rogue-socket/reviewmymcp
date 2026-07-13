"""Deterministic edge case probes — no LLM needed."""

from __future__ import annotations

from typing import Any

from .schema import ToolDefinition


_MAX_TOOLS_FOR_PER_TOOL_PROBES = 3

_WRITE_PREFIXES = (
    "add_",
    "clear_",
    "create_",
    "delete_",
    "destroy_",
    "edit_",
    "execute_",
    "fork_",
    "insert_",
    "merge_",
    "move_",
    "patch_",
    "post_",
    "publish_",
    "purge_",
    "push_",
    "remove_",
    "reset_",
    "run_",
    "set_",
    "truncate_",
    "update_",
    "upload_",
    "write_",
)


def _is_read_only(tool: ToolDefinition) -> bool:
    return not tool.name.startswith(_WRITE_PREFIXES)


def generate_edge_probes(tools: list[ToolDefinition]) -> list[dict[str, Any]]:
    probes: list[dict[str, Any]] = []

    # Only probe read-only tools. Malformed calls against mutators can still cause
    # side effects on permissive servers. Cap probes so edge noise stays bounded.
    read_tools = [t for t in tools if _is_read_only(t)]
    sample = read_tools[:_MAX_TOOLS_FOR_PER_TOOL_PROBES]
    for tool in sample:
        probes.extend(_missing_required_args(tool))
        probes.extend(_wrong_type_args(tool))
        probes.extend(_empty_args(tool))
        probes.extend(_extra_args(tool))

    probes.extend(_nonexistent_tool())
    probes.append(_malformed_jsonrpc())
    probes.append(_missing_method())

    return probes


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

    for f in required:
        partial_args = {
            r: _default_value(tool.input_schema.get("properties", {}).get(r, {}))
            for r in required
            if r != f
        }
        probes.append(
            {
                "probe_type": "missing_required_args",
                "description": f"Call `{tool.name}` missing required field `{f}`",
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

    for f, schema in properties.items():
        if f not in required:
            continue
        declared_type = schema.get("type", "string")
        wrong_value = _wrong_type_value(declared_type)
        if wrong_value is None:
            continue

        args = {r: _default_value(properties.get(r, {})) for r in required}
        args[f] = wrong_value

        probes.append(
            {
                "probe_type": "wrong_type_args",
                "description": f"Call `{tool.name}` with wrong type for `{f}` (expected {declared_type})",
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
