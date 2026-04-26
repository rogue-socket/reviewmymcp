"""JSON-RPC message parsing and classification."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ParsedMessage:
    """A classified JSON-RPC 2.0 message."""

    is_request: bool = False
    is_response: bool = False
    is_notification: bool = False
    is_error: bool = False

    jsonrpc_id: int | str | None = None
    method: str | None = None
    params: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    raw: dict[str, Any] = field(default_factory=dict)


def parse_jsonrpc(msg: dict[str, Any]) -> ParsedMessage:
    """Classify a raw JSON-RPC 2.0 message dict."""
    parsed = ParsedMessage(raw=msg)
    parsed.jsonrpc_id = msg.get("id")
    parsed.method = msg.get("method")
    parsed.params = msg.get("params")
    parsed.result = msg.get("result")
    parsed.error = msg.get("error")

    has_id = "id" in msg
    has_method = "method" in msg
    has_result = "result" in msg
    has_error = "error" in msg

    if has_method and has_id:
        parsed.is_request = True
    elif has_method and not has_id:
        parsed.is_notification = True
    elif has_id and (has_result or has_error):
        parsed.is_response = True
        if has_error:
            parsed.is_error = True

    return parsed
