"""Tests for JSON-RPC message parser."""

from reviewmymcp.ingest.parser import parse_jsonrpc


def test_parse_request():
    msg = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "test"}}
    parsed = parse_jsonrpc(msg)
    assert parsed.is_request is True
    assert parsed.is_response is False
    assert parsed.is_notification is False
    assert parsed.method == "tools/call"
    assert parsed.jsonrpc_id == 1
    assert parsed.params == {"name": "test"}


def test_parse_response_success():
    msg = {"jsonrpc": "2.0", "id": 1, "result": {"content": []}}
    parsed = parse_jsonrpc(msg)
    assert parsed.is_response is True
    assert parsed.is_request is False
    assert parsed.is_error is False
    assert parsed.result == {"content": []}


def test_parse_response_error():
    msg = {"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "Not found"}}
    parsed = parse_jsonrpc(msg)
    assert parsed.is_response is True
    assert parsed.is_error is True
    assert parsed.error["code"] == -32601


def test_parse_notification():
    msg = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    parsed = parse_jsonrpc(msg)
    assert parsed.is_notification is True
    assert parsed.is_request is False
    assert parsed.jsonrpc_id is None
    assert parsed.method == "notifications/initialized"


def test_parse_empty_message():
    parsed = parse_jsonrpc({})
    assert parsed.is_request is False
    assert parsed.is_response is False
    assert parsed.is_notification is False


def test_parse_preserves_raw():
    msg = {"jsonrpc": "2.0", "id": 5, "method": "ping"}
    parsed = parse_jsonrpc(msg)
    assert parsed.raw == msg


def test_parse_string_id():
    msg = {"jsonrpc": "2.0", "id": "abc-123", "method": "tools/list", "params": {}}
    parsed = parse_jsonrpc(msg)
    assert parsed.jsonrpc_id == "abc-123"
    assert parsed.is_request is True
