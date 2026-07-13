"""HTTP transparent proxy — reverse proxy for streamable HTTP MCP servers."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse
from starlette.routing import Route

from reviewmymcp.ingest.normalizer import normalize_event
from reviewmymcp.ingest.redactor import redact_dict
from reviewmymcp.ingest.schema import Direction, McpEvent, Transport


class HttpProxy:
    """Transparent HTTP reverse proxy that captures MCP traffic."""

    def __init__(
        self,
        upstream_url: str,
        redact: bool = True,
        extra_redaction_patterns: list[str] | None = None,
    ):
        self._upstream = upstream_url.rstrip("/")
        self._redact = redact
        self._extra_patterns = extra_redaction_patterns or []
        self._events: list[McpEvent] = []
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=30.0))
        self._log_callbacks: list[Any] = []

    @property
    def events(self) -> list[McpEvent]:
        return list(self._events)

    def on_event(self, callback: Any) -> None:
        self._log_callbacks.append(callback)

    def build_app(self) -> Starlette:
        return Starlette(
            routes=[
                Route("/{path:path}", self._handle_request, methods=["GET", "POST", "DELETE"]),
                Route("/", self._handle_request, methods=["GET", "POST", "DELETE"]),
            ],
        )

    async def _handle_request(self, request: Request) -> Response:
        path = request.url.path
        upstream_url = f"{self._upstream}{path}"
        if request.url.query:
            upstream_url += f"?{request.url.query}"

        headers = dict(request.headers)
        headers.pop("host", None)

        body = await request.body()

        session_id = headers.get("mcp-session-id")

        if body:
            self._capture_request(body, headers, session_id)

        upstream_request = self._client.build_request(
            method=request.method,
            url=upstream_url,
            headers=headers,
            content=body,
        )
        upstream_response = await self._client.send(upstream_request, stream=True)

        response_headers = dict(upstream_response.headers)
        if not session_id:
            session_id = response_headers.get("mcp-session-id")

        content_type = response_headers.get("content-type", "")

        if "text/event-stream" in content_type:
            return self._stream_sse_response(upstream_response, session_id)

        response_body = await upstream_response.aread()
        await upstream_response.aclose()
        self._capture_response(response_body, response_headers, session_id, upstream_response.status_code)

        return Response(
            content=response_body,
            status_code=upstream_response.status_code,
            headers={
                k: v
                for k, v in response_headers.items()
                if k.lower() not in ("content-length", "transfer-encoding", "content-encoding")
            },
        )

    def _stream_sse_response(self, upstream_response: httpx.Response, session_id: str | None) -> StreamingResponse:
        async def event_stream():
            try:
                sse_buffer = ""
                async for chunk in upstream_response.aiter_text():
                    sse_buffer += chunk
                    while "\n\n" in sse_buffer:
                        event_text, sse_buffer = sse_buffer.split("\n\n", 1)
                        self._capture_sse_event(event_text, session_id)
                        yield event_text + "\n\n"
                if sse_buffer:
                    self._capture_sse_event(sse_buffer, session_id)
                    yield sse_buffer
            finally:
                await upstream_response.aclose()

        return StreamingResponse(
            event_stream(),
            status_code=upstream_response.status_code,
            headers={
                key: value
                for key, value in upstream_response.headers.items()
                if key.lower() not in ("content-length", "transfer-encoding", "content-encoding")
            },
        )

    def _capture_request(self, body: bytes, headers: dict, session_id: str | None) -> None:
        try:
            raw = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return

        messages = raw if isinstance(raw, list) else [raw]
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            self._record_event(msg, Direction.CLIENT_TO_SERVER, session_id, http_headers=headers)

    def _capture_response(self, body: bytes, headers: dict, session_id: str | None, status_code: int) -> None:
        try:
            raw = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return

        messages = raw if isinstance(raw, list) else [raw]
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            self._record_event(
                msg,
                Direction.SERVER_TO_CLIENT,
                session_id,
                http_headers=headers,
                http_status=status_code,
            )

    def _capture_sse_event(self, event_text: str, session_id: str | None) -> None:
        data_lines = []
        event_id = None
        for line in event_text.split("\n"):
            if line.startswith("data:"):
                data_lines.append(line[5:].strip())
            elif line.startswith("id:"):
                event_id = line[3:].strip()

        if not data_lines:
            return

        data_str = "\n".join(data_lines)
        try:
            raw = json.loads(data_str)
        except json.JSONDecodeError:
            return

        if not isinstance(raw, dict):
            return

        self._record_event(
            raw,
            Direction.SERVER_TO_CLIENT,
            session_id,
            sse_event_id=event_id,
        )

    def _record_event(
        self,
        raw: dict[str, Any],
        direction: Direction,
        session_id: str | None,
        http_headers: dict | None = None,
        http_status: int | None = None,
        sse_event_id: str | None = None,
    ) -> None:
        if self._redact:
            raw, redacted_fields = redact_dict(raw, extra_patterns=self._extra_patterns)
        else:
            redacted_fields = []

        http_meta = {}
        if http_headers:
            safe_headers = {
                k: v
                for k, v in http_headers.items()
                if k.lower() in ("content-type", "mcp-session-id", "mcp-protocol-version", "accept")
            }
            http_meta["http_headers"] = safe_headers
        if http_status:
            http_meta["http_status"] = http_status
        if sse_event_id:
            http_meta["sse_event_id"] = sse_event_id

        event = normalize_event(
            raw=raw,
            transport=Transport.HTTP,
            direction=direction,
            session_id=session_id,
            timestamp=datetime.now(UTC),
            http_metadata=http_meta if http_meta else None,
        )
        event.redacted_fields = redacted_fields
        self._events.append(event)

        for cb in self._log_callbacks:
            cb(event)


def create_proxy_app(upstream_url: str, redact: bool = True) -> tuple[Starlette, HttpProxy]:
    proxy = HttpProxy(upstream_url=upstream_url, redact=redact)
    app = proxy.build_app()
    return app, proxy
