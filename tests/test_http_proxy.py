"""Tests for the HTTP transparent proxy."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import StreamingResponse
from starlette.routing import Route

from reviewmymcp.proxy.http_proxy import create_proxy_app


@pytest.mark.asyncio
async def test_streaming_post_is_forwarded_once_and_preserves_session_header():
    calls = 0

    async def upstream_mcp(request: Request) -> StreamingResponse:
        nonlocal calls
        calls += 1
        assert await request.json() == {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {}}

        async def stream():
            yield b'data: {"jsonrpc":"2.0","id":1,"result":{}}\n\n'

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"mcp-session-id": "upstream-session"},
        )

    upstream = Starlette(routes=[Route("/mcp", upstream_mcp, methods=["POST"])])
    app, proxy = create_proxy_app("http://upstream")
    proxy._client = httpx.AsyncClient(transport=httpx.ASGITransport(app=upstream), base_url="http://upstream")

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://proxy") as client:
        response = await client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {}})

    await proxy._client.aclose()

    assert response.status_code == 200
    assert response.headers["mcp-session-id"] == "upstream-session"
    assert response.text == 'data: {"jsonrpc":"2.0","id":1,"result":{}}\n\n'
    assert calls == 1


@pytest.mark.asyncio
async def test_streaming_response_closes_upstream_on_client_cancellation():
    class BlockingStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"jsonrpc":"2.0","id":1,"result":{}}\n\n'
            await asyncio.Event().wait()

        async def aclose(self):
            return None

    _, proxy = create_proxy_app("http://upstream")
    upstream_response = httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        stream=BlockingStream(),
    )
    response = proxy._stream_sse_response(upstream_response, "session-1")
    body_iterator = response.body_iterator

    assert await anext(body_iterator) == 'data: {"jsonrpc":"2.0","id":1,"result":{}}\n\n'
    await body_iterator.aclose()

    assert upstream_response.is_closed is True
    await proxy._client.aclose()
