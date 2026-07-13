"""Convert parsed JSON-RPC messages into McpEvent objects and extract metadata."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from reviewmymcp.ingest.parser import parse_jsonrpc
from reviewmymcp.ingest.schema import (
    ClientCapabilities,
    Direction,
    McpEvent,
    ServerCapabilities,
    ServerMeta,
    ToolDefinition,
    Transport,
)


def normalize_event(
    raw: dict[str, Any],
    transport: Transport = Transport.STDIO,
    direction: Direction = Direction.CLIENT_TO_SERVER,
    session_id: str | None = None,
    timestamp: datetime | None = None,
    http_metadata: dict[str, Any] | None = None,
) -> McpEvent:
    """Build an McpEvent from a raw JSON-RPC message dict."""
    parsed = parse_jsonrpc(raw)

    event = McpEvent(
        event_id=str(uuid4()),
        timestamp=timestamp or datetime.now(UTC),
        session_id=session_id,
        transport=transport,
        direction=direction,
        jsonrpc_id=parsed.jsonrpc_id,
        method=parsed.method,
        is_request=parsed.is_request,
        is_response=parsed.is_response,
        is_notification=parsed.is_notification,
        is_error=parsed.is_error,
        params=parsed.params,
        result=parsed.result,
        error=parsed.error,
        raw_size_bytes=len(json.dumps(raw).encode()),
        raw_message=raw,
    )
    event.is_stress = bool(raw.get("is_stress", False))

    if http_metadata:
        event.http_method = http_metadata.get("http_method")
        event.http_status = http_metadata.get("http_status")
        event.http_headers = http_metadata.get("http_headers")
        event.content_type = http_metadata.get("content_type")
        event.sse_event_id = http_metadata.get("sse_event_id")

    meta = _extract_meta(raw)
    if meta:
        task_info = meta.get("io.modelcontextprotocol/related-task", {})
        if task_info:
            event.task_id = task_info.get("taskId")

    return event


def _extract_meta(raw: dict[str, Any]) -> dict[str, Any] | None:
    for key in ("result", "params"):
        container = raw.get(key)
        if isinstance(container, dict) and "_meta" in container:
            return container["_meta"]
    return None


def extract_server_meta(events: list[McpEvent]) -> ServerMeta:
    """Extract ServerMeta from initialize handshake events and tools/list responses."""
    meta = ServerMeta()

    for event in events:
        if event.is_request and event.method == "initialize" and event.params:
            client_caps = event.params.get("capabilities", {})
            meta.client_capabilities = ClientCapabilities.from_capabilities_dict(client_caps)

        if event.is_response and event.result and not event.is_error:
            result = event.result
            if "serverInfo" in result:
                server_info = result["serverInfo"]
                meta.server_name = server_info.get("name", "")
                meta.server_version = server_info.get("version", "")
                meta.protocol_version = result.get("protocolVersion", "")
                meta.instructions = result.get("instructions", "")
                caps = result.get("capabilities", {})
                meta.server_capabilities = ServerCapabilities.from_capabilities_dict(caps)

    tools = extract_tool_definitions(events)
    if tools:
        meta.tools = tools
        meta.auth.scopes_required = {tool.name: tool.required_scopes for tool in tools if tool.required_scopes}

    scopes_used = extract_auth_scopes_used(events)
    if scopes_used:
        meta.auth.scopes_used = scopes_used

    return meta


def extract_tool_definitions(events: list[McpEvent]) -> list[ToolDefinition]:
    """Extract ToolDefinition list from tools/list response events."""
    tools: list[ToolDefinition] = []
    seen_names: set[str] = set()

    for event in events:
        if not (event.is_response and event.result and not event.is_error):
            continue
        result = event.result
        if "tools" not in result:
            continue
        for raw_tool in result["tools"]:
            name = raw_tool.get("name", "")
            if name and name not in seen_names:
                seen_names.add(name)
                tools.append(
                    ToolDefinition(
                        name=name,
                        description=raw_tool.get("description", ""),
                        input_schema=raw_tool.get("inputSchema", {}),
                        output_schema=raw_tool.get("outputSchema", {}),
                        annotations=raw_tool.get("annotations", {}),
                        execution=raw_tool.get("execution", {}),
                        required_scopes=_extract_required_scopes(raw_tool),
                    )
                )
    return tools


def extract_auth_scopes_used(events: list[McpEvent]) -> list[str]:
    """Extract observed auth scopes from metadata-bearing responses when available."""
    scopes: set[str] = set()
    for event in events:
        if not (event.is_response and event.result and not event.is_error):
            continue
        scopes.update(_find_scope_list(event.result))
    return sorted(scopes)


def _extract_required_scopes(raw_tool: dict[str, Any]) -> list[str]:
    annotations = raw_tool.get("annotations")
    if not isinstance(annotations, dict):
        annotations = {}
    meta = raw_tool.get("_meta")
    if not isinstance(meta, dict):
        meta = {}

    scope_values = (
        raw_tool.get("requiredScopes")
        or raw_tool.get("required_scopes")
        or annotations.get("requiredScopes")
        or annotations.get("required_scopes")
        or meta.get("requiredScopes")
        or meta.get("required_scopes")
        or []
    )
    return _normalize_scope_list(scope_values)


def _find_scope_list(value: Any) -> set[str]:
    if not isinstance(value, dict):
        return set()

    scopes: set[str] = set()
    auth = value.get("auth")
    if isinstance(auth, dict):
        for key in ("scopes_used", "scopesUsed", "scopes"):
            scopes.update(_normalize_scope_list(auth.get(key)))

    meta = value.get("_meta")
    if isinstance(meta, dict):
        scopes.update(_find_scope_list(meta))

    for key in ("scopes_used", "scopesUsed"):
        scopes.update(_normalize_scope_list(value.get(key)))

    return scopes


def _normalize_scope_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return sorted({item for item in value if isinstance(item, str) and item})
    return []
