"""Canonical event schema — plain dataclasses, no external deps."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class ToolDefinition:
    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)


@dataclass
class McpEvent:
    event_id: str
    timestamp: datetime
    session_id: str | None = None
    transport: str = "stdio"
    direction: str = "client_to_server"

    jsonrpc_id: int | str | None = None
    method: str | None = None
    is_request: bool = False
    is_response: bool = False
    is_notification: bool = False
    is_error: bool = False

    params: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None

    latency_ms: float | None = None
    request_event_id: str | None = None

    raw_size_bytes: int = 0
    raw_message: dict[str, Any] | None = None

    is_probe: bool = False
    probe_type: str | None = None

    def to_ndjson_dict(self) -> dict[str, Any]:
        """Produce the NDJSON wrapper dict the review tool expects."""
        base = dict(self.raw_message) if self.raw_message else {}
        base["direction"] = self.direction
        base["timestamp"] = self.timestamp.isoformat()
        if self.session_id:
            base["session_id"] = self.session_id
        if self.is_probe:
            base["is_probe"] = True
            if self.probe_type:
                base["probe_type"] = self.probe_type
        return base

    def to_ndjson_line(self) -> str:
        return json.dumps(self.to_ndjson_dict(), default=str)
