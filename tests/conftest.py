"""Shared test fixtures and helpers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from reviewmymcp.evaluators.base import EvaluatorConfig
from reviewmymcp.ingest.schema import (
    ClientCapabilities,
    Direction,
    McpEvent,
    ServerCapabilities,
    ServerMeta,
    ToolDefinition,
    Transport,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"

_counter = 0


def _next_id() -> str:
    global _counter
    _counter += 1
    return f"evt-{_counter}"


def make_event(
    method: str | None = None,
    is_request: bool = False,
    is_response: bool = False,
    is_notification: bool = False,
    is_error: bool = False,
    params: dict | None = None,
    result: dict | None = None,
    error: dict | None = None,
    jsonrpc_id: int | str | None = None,
    session_id: str = "test-session",
    direction: Direction = Direction.CLIENT_TO_SERVER,
    timestamp: datetime | None = None,
    latency_ms: float | None = None,
    request_event_id: str | None = None,
    raw_size_bytes: int = 100,
    raw_message: dict | None = None,
    http_headers: dict | None = None,
    event_id: str | None = None,
    task_id: str | None = None,
    is_probe: bool = False,
    probe_type: str | None = None,
    is_stress: bool = False,
) -> McpEvent:
    return McpEvent(
        event_id=event_id or _next_id(),
        timestamp=timestamp or datetime.now(UTC),
        session_id=session_id,
        transport=Transport.STDIO,
        direction=direction,
        method=method,
        is_request=is_request,
        is_response=is_response,
        is_notification=is_notification,
        is_error=is_error,
        params=params,
        result=result,
        error=error,
        jsonrpc_id=jsonrpc_id,
        latency_ms=latency_ms,
        request_event_id=request_event_id,
        raw_size_bytes=raw_size_bytes,
        raw_message=raw_message,
        http_headers=http_headers,
        task_id=task_id,
        is_probe=is_probe,
        probe_type=probe_type,
        is_stress=is_stress,
    )


def make_tool_call_pair(
    tool_name: str,
    arguments: dict | None = None,
    result_content: list | None = None,
    is_error: bool = False,
    latency_ms: float = 100.0,
    session_id: str = "test-session",
    base_time: datetime | None = None,
    request_id: int = 1,
    is_probe: bool = False,
    probe_type: str | None = None,
    is_stress: bool = False,
) -> tuple[McpEvent, McpEvent]:
    base = base_time or datetime.now(UTC)
    req_eid = str(uuid4())

    req = make_event(
        event_id=req_eid,
        method="tools/call",
        is_request=True,
        params={"name": tool_name, "arguments": arguments or {}},
        jsonrpc_id=request_id,
        session_id=session_id,
        timestamp=base,
        is_probe=is_probe,
        probe_type=probe_type,
        is_stress=is_stress,
    )

    if result_content is None:
        result_content = [{"type": "text", "text": '{"ok": true}'}]

    resp = make_event(
        method="tools/call",
        is_response=True,
        is_error=is_error,
        result={"content": result_content, "isError": is_error} if not is_error else None,
        error={"code": -32603, "message": "Internal error"} if is_error else None,
        jsonrpc_id=request_id,
        session_id=session_id,
        timestamp=base + timedelta(milliseconds=latency_ms),
        latency_ms=latency_ms,
        request_event_id=req_eid,
        direction=Direction.SERVER_TO_CLIENT,
        is_probe=is_probe,
        probe_type=probe_type,
        is_stress=is_stress,
    )

    return req, resp


def make_init_pair(session_id: str = "test-session", base_time: datetime | None = None) -> tuple[McpEvent, McpEvent]:
    base = base_time or datetime.now(UTC)
    req_eid = str(uuid4())

    req = make_event(
        event_id=req_eid,
        method="initialize",
        is_request=True,
        params={
            "protocolVersion": "2025-11-25",
            "capabilities": {"roots": {"listChanged": True}, "sampling": {}},
            "clientInfo": {"name": "TestClient", "version": "1.0"},
        },
        jsonrpc_id=1,
        session_id=session_id,
        timestamp=base,
    )

    resp = make_event(
        method="initialize",
        is_response=True,
        result={
            "protocolVersion": "2025-11-25",
            "capabilities": {"tools": {"listChanged": True}, "logging": {}},
            "serverInfo": {"name": "TestServer", "version": "2.0"},
        },
        jsonrpc_id=1,
        session_id=session_id,
        timestamp=base + timedelta(milliseconds=50),
        request_event_id=req_eid,
        direction=Direction.SERVER_TO_CLIENT,
    )

    return req, resp


def make_tools_list_pair(
    tools: list[dict],
    session_id: str = "test-session",
    base_time: datetime | None = None,
) -> tuple[McpEvent, McpEvent]:
    base = base_time or datetime.now(UTC)
    req_eid = str(uuid4())

    req = make_event(
        event_id=req_eid,
        method="tools/list",
        is_request=True,
        params={},
        jsonrpc_id=2,
        session_id=session_id,
        timestamp=base,
    )

    resp = make_event(
        method="tools/list",
        is_response=True,
        result={"tools": tools},
        jsonrpc_id=2,
        session_id=session_id,
        timestamp=base + timedelta(milliseconds=20),
        request_event_id=req_eid,
        direction=Direction.SERVER_TO_CLIENT,
    )

    return req, resp


def make_server_meta(tools: list[ToolDefinition] | None = None) -> ServerMeta:
    return ServerMeta(
        server_name="TestServer",
        server_version="2.0",
        protocol_version="2025-11-25",
        server_capabilities=ServerCapabilities(tools=True, logging=True),
        client_capabilities=ClientCapabilities(roots=True, sampling=True),
        tools=tools or [],
    )


def make_tool_def(
    name: str = "test_tool",
    description: str = "A test tool",
    properties: dict | None = None,
    required: list[str] | None = None,
    output_schema: dict | None = None,
    annotations: dict | None = None,
    required_scopes: list[str] | None = None,
) -> ToolDefinition:
    schema = {"type": "object", "properties": properties or {}, "required": required or []}
    return ToolDefinition(
        name=name,
        description=description,
        input_schema=schema,
        output_schema=output_schema or {},
        annotations=annotations or {},
        required_scopes=required_scopes or [],
    )


@pytest.fixture
def sample_stdio_log() -> Path:
    return FIXTURES_DIR / "sample_stdio_log.ndjson"


@pytest.fixture
def eval_config() -> EvaluatorConfig:
    return EvaluatorConfig()
