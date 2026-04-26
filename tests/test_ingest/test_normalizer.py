"""Tests for event normalization and metadata extraction."""

from reviewmymcp.ingest.normalizer import extract_server_meta, extract_tool_definitions, normalize_event
from reviewmymcp.ingest.schema import Direction, Transport

from tests.conftest import make_event, make_init_pair, make_tools_list_pair


def test_normalize_request():
    raw = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "search"}}
    event = normalize_event(raw, transport=Transport.STDIO, direction=Direction.CLIENT_TO_SERVER)
    assert event.is_request is True
    assert event.method == "tools/call"
    assert event.transport == Transport.STDIO
    assert event.event_id is not None
    assert event.raw_size_bytes > 0


def test_normalize_with_http_metadata():
    raw = {"jsonrpc": "2.0", "id": 1, "result": {}}
    event = normalize_event(
        raw,
        transport=Transport.HTTP,
        http_metadata={"http_status": 200, "http_headers": {"content-type": "application/json"}},
    )
    assert event.http_status == 200
    assert event.http_headers == {"content-type": "application/json"}


def test_normalize_extracts_task_id():
    raw = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"_meta": {"io.modelcontextprotocol/related-task": {"taskId": "task-123"}}},
    }
    event = normalize_event(raw)
    assert event.task_id == "task-123"


def test_extract_server_meta():
    init_req, init_resp = make_init_pair()
    tools_req, tools_resp = make_tools_list_pair([
        {"name": "search", "description": "Search things", "inputSchema": {"type": "object"}},
    ])

    meta = extract_server_meta([init_req, init_resp, tools_req, tools_resp])
    assert meta.server_name == "TestServer"
    assert meta.server_version == "2.0"
    assert meta.protocol_version == "2025-11-25"
    assert meta.server_capabilities.tools is True
    assert meta.client_capabilities.sampling is True
    assert len(meta.tools) == 1
    assert meta.tools[0].name == "search"


def test_extract_tool_definitions():
    tools_req, tools_resp = make_tools_list_pair([
        {"name": "a", "description": "Tool A", "inputSchema": {}},
        {"name": "b", "description": "Tool B", "inputSchema": {}},
    ])
    tools = extract_tool_definitions([tools_req, tools_resp])
    assert len(tools) == 2
    assert {t.name for t in tools} == {"a", "b"}


def test_extract_tool_definitions_deduplicates():
    r1, resp1 = make_tools_list_pair([{"name": "a", "description": "A", "inputSchema": {}}])
    r2, resp2 = make_tools_list_pair([{"name": "a", "description": "A again", "inputSchema": {}}])
    tools = extract_tool_definitions([r1, resp1, r2, resp2])
    assert len(tools) == 1
