"""Load MCP log files in NDJSON or JSON array format."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from reviewmymcp.ingest.normalizer import normalize_event
from reviewmymcp.ingest.redactor import redact_dict
from reviewmymcp.ingest.schema import Direction, McpEvent, Transport


def _parse_direction(raw: Any) -> Direction:
    if isinstance(raw, str):
        normalized = raw.lower().replace("-", "_").replace(" ", "_")
        if "client_to_server" in normalized:
            return Direction.CLIENT_TO_SERVER
        if "server_to_client" in normalized:
            return Direction.SERVER_TO_CLIENT
        if normalized.startswith("client"):
            return Direction.CLIENT_TO_SERVER
        if normalized.startswith("server"):
            return Direction.SERVER_TO_CLIENT
    return Direction.CLIENT_TO_SERVER


def _unwrap_record(
    raw: dict[str, Any],
) -> tuple[dict[str, Any], Direction, datetime | None, str | None, bool, str | None]:
    """Unwrap optional wrapper objects to extract the JSON-RPC message, direction, timestamp, session, probe flags."""
    direction = Direction.CLIENT_TO_SERVER
    timestamp = None
    session_id = None
    is_probe = bool(raw.get("is_probe", False))
    probe_type = raw.get("probe_type") if is_probe else None

    if "direction" in raw:
        direction = _parse_direction(raw["direction"])
    if "timestamp" in raw:
        ts = raw["timestamp"]
        if isinstance(ts, str):
            try:
                timestamp = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except ValueError:
                pass
        elif isinstance(ts, (int, float)):
            timestamp = datetime.fromtimestamp(ts, tz=UTC)
    if "session_id" in raw:
        session_id = raw.get("session_id")

    if "message" in raw and isinstance(raw["message"], dict):
        return raw["message"], direction, timestamp, session_id, is_probe, probe_type
    if "jsonrpc" in raw:
        return raw, direction, timestamp, session_id, is_probe, probe_type

    return raw, direction, timestamp, session_id, is_probe, probe_type


def load_file(
    path: str | Path,
    transport: Transport = Transport.STDIO,
    redact: bool = True,
    extra_redaction_patterns: list[str] | None = None,
) -> list[McpEvent]:
    """Load a log file and return normalized McpEvent list."""
    path = Path(path)
    content = path.read_text(encoding="utf-8").strip()

    records: list[dict[str, Any]] = []

    if content.startswith("["):
        records = json.loads(content)
    else:
        for line in content.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    events: list[McpEvent] = []
    for raw_record in records:
        if not isinstance(raw_record, dict):
            continue

        message, direction, timestamp, session_id, is_probe, probe_type = _unwrap_record(raw_record)

        if redact:
            message, redacted_fields = redact_dict(message, extra_patterns=extra_redaction_patterns)
        else:
            redacted_fields = []

        event = normalize_event(
            raw=message,
            transport=transport,
            direction=direction,
            session_id=session_id,
            timestamp=timestamp,
        )
        event.redacted_fields = redacted_fields
        event.is_probe = is_probe
        event.probe_type = probe_type
        events.append(event)

    return events
