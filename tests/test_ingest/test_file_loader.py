"""Tests for log file loading."""

import json
import tempfile
from pathlib import Path

from reviewmymcp.ingest.file_loader import load_file
from reviewmymcp.ingest.schema import Direction


def test_load_ndjson():
    lines = [
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}, "direction": "client_to_server"}),
        json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"serverInfo": {"name": "S"}}, "direction": "server_to_client"}),
    ]
    with tempfile.NamedTemporaryFile(mode="w", suffix=".ndjson", delete=False) as f:
        f.write("\n".join(lines))
        path = f.name

    events = load_file(path, redact=False)
    assert len(events) == 2
    assert events[0].is_request is True
    assert events[0].direction == Direction.CLIENT_TO_SERVER
    assert events[1].is_response is True
    assert events[1].direction == Direction.SERVER_TO_CLIENT

    Path(path).unlink()


def test_load_json_array():
    data = [
        {"jsonrpc": "2.0", "id": 1, "method": "ping"},
        {"jsonrpc": "2.0", "id": 1, "result": {}},
    ]
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(data, f)
        path = f.name

    events = load_file(path, redact=False)
    assert len(events) == 2

    Path(path).unlink()


def test_load_with_wrapper_object():
    lines = [
        json.dumps({
            "direction": "client_to_server",
            "timestamp": "2025-01-15T10:00:00Z",
            "session_id": "s1",
            "message": {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        }),
    ]
    with tempfile.NamedTemporaryFile(mode="w", suffix=".ndjson", delete=False) as f:
        f.write("\n".join(lines))
        path = f.name

    events = load_file(path, redact=False)
    assert len(events) == 1
    assert events[0].method == "tools/list"
    assert events[0].session_id == "s1"

    Path(path).unlink()


def test_load_with_timestamp_parsing():
    line = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "ping",
        "timestamp": "2025-06-15T12:30:00Z",
        "direction": "client_to_server",
    })
    with tempfile.NamedTemporaryFile(mode="w", suffix=".ndjson", delete=False) as f:
        f.write(line)
        path = f.name

    events = load_file(path, redact=False)
    assert events[0].timestamp.year == 2025
    assert events[0].timestamp.month == 6

    Path(path).unlink()


def test_load_with_redaction():
    line = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"content": [{"type": "text", "text": "key: sk-abcdefghijklmnopqrstuvwxyz"}]},
        "direction": "server_to_client",
    })
    with tempfile.NamedTemporaryFile(mode="w", suffix=".ndjson", delete=False) as f:
        f.write(line)
        path = f.name

    events = load_file(path, redact=True)
    assert len(events[0].redacted_fields) > 0

    Path(path).unlink()


def test_load_skips_invalid_json():
    content = '{"jsonrpc":"2.0","id":1,"method":"ping"}\nnot json\n{"jsonrpc":"2.0","id":2,"result":{}}'
    with tempfile.NamedTemporaryFile(mode="w", suffix=".ndjson", delete=False) as f:
        f.write(content)
        path = f.name

    events = load_file(path, redact=False)
    assert len(events) == 2

    Path(path).unlink()


def test_load_sample_fixture(sample_stdio_log):
    events = load_file(sample_stdio_log, redact=False)
    assert len(events) > 0
    init_events = [e for e in events if e.method == "initialize"]
    assert len(init_events) >= 1
