"""Request-response matching, latency computation, and task ID extraction."""

from __future__ import annotations

from reviewmymcp.ingest.schema import McpEvent


def correlate(events: list[McpEvent]) -> list[McpEvent]:
    """Match requests to responses, compute latency, extract task IDs. Mutates events in place."""
    request_index: dict[tuple[str | None, int | str], McpEvent] = {}

    for event in events:
        if event.is_request and event.jsonrpc_id is not None:
            key = (event.session_id, event.jsonrpc_id)
            request_index[key] = event

    for event in events:
        if event.is_response and event.jsonrpc_id is not None:
            key = (event.session_id, event.jsonrpc_id)
            request = request_index.get(key)
            if request:
                event.request_event_id = request.event_id
                if event.method is None:
                    event.method = request.method
                delta = (event.timestamp - request.timestamp).total_seconds() * 1000
                if delta >= 0:
                    event.latency_ms = delta

        _extract_task_id(event)

    return events


def _extract_task_id(event: McpEvent) -> None:
    """Extract task_id from _meta field if present."""
    for container in (event.result, event.params):
        if not isinstance(container, dict):
            continue
        meta = container.get("_meta")
        if not isinstance(meta, dict):
            continue
        task_info = meta.get("io.modelcontextprotocol/related-task")
        if isinstance(task_info, dict):
            tid = task_info.get("taskId")
            if tid:
                event.task_id = str(tid)
                return


def get_sessions(events: list[McpEvent]) -> dict[str | None, list[McpEvent]]:
    """Group events by session_id."""
    sessions: dict[str | None, list[McpEvent]] = {}
    for event in events:
        sessions.setdefault(event.session_id, []).append(event)
    return sessions
