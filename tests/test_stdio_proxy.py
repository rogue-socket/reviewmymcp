"""Tests for stdio proxy capture."""

import json

from reviewmymcp.ingest.schema import Direction
from reviewmymcp.proxy.stdio_proxy import StdioProxy


def test_stdio_proxy_redacts_and_persists_captured_traffic(tmp_path):
    log_path = tmp_path / "traffic.ndjson"
    proxy = StdioProxy(["unused"], log_file=log_path, session_id="session-1")
    proxy._log_handle = log_path.open("a", encoding="utf-8")

    proxy._capture(
        b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"access_token":"short-token"}}\n',
        Direction.CLIENT_TO_SERVER,
    )
    proxy._log_handle.close()

    assert proxy.events[0].params == {"access_token": "[REDACTED:header]"}
    persisted = json.loads(log_path.read_text())
    assert persisted["params"] == {"access_token": "[REDACTED:header]"}
