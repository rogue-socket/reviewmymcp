"""Async stdio driver for MCP servers — captures all JSON-RPC traffic."""

from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from .edge_probes import generate_edge_probes
from .schema import McpEvent, ToolDefinition

CLIENT_TO_SERVER = "client_to_server"
SERVER_TO_CLIENT = "server_to_client"


class StdioAgentDriver:
    def __init__(
        self,
        server_command: list[str],
        session_id: str | None = None,
        env: dict[str, str] | None = None,
    ):
        self._server_command = server_command
        self._events: list[McpEvent] = []
        self._process: asyncio.subprocess.Process | None = None
        self._request_id = 0
        self._session_id = session_id or str(uuid4())
        self._pending: dict[int | str, asyncio.Future] = {}
        self._send_times: dict[int | str, float] = {}
        self._env = {**os.environ, **(env or {})}
        self._tools: list[ToolDefinition] = []
        self._probe_context: str | None = None
        self._probe_rids: dict[int | str, str] = {}

    @property
    def events(self) -> list[McpEvent]:
        return list(self._events)

    @property
    def tools(self) -> list[ToolDefinition]:
        return list(self._tools)

    async def start(self) -> None:
        self._process = await asyncio.create_subprocess_exec(
            *self._server_command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self._env,
            limit=10 * 1024 * 1024,  # 10MB line limit for large JSON-RPC responses
        )
        self._reader_task = asyncio.create_task(self._read_responses())

    async def stop(self) -> None:
        if self._process and self._process.stdin:
            self._process.stdin.close()
        if self._process:
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except TimeoutError:
                self._process.kill()
        if hasattr(self, "_reader_task"):
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass

    async def initialize(self) -> dict[str, Any] | None:
        return await self._send_request(
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {"roots": {"listChanged": True}, "sampling": {}},
                "clientInfo": {"name": "reviewmymcp-collector", "version": "0.1.0"},
            },
        )

    async def send_initialized(self) -> None:
        await self._send_notification("notifications/initialized")

    async def list_tools(self) -> dict[str, Any] | None:
        result = await self._send_request("tools/list", {})
        if result and "result" in result:
            self._tools = [
                ToolDefinition(
                    name=t["name"],
                    description=t.get("description", ""),
                    input_schema=t.get("inputSchema", {}),
                )
                for t in result["result"].get("tools", [])
            ]
        return result

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any] | None:
        return await self._send_request("tools/call", {"name": name, "arguments": arguments})

    async def execute_scenario(self, scenario: dict[str, Any]) -> list[dict[str, Any]]:
        results = []
        for step in scenario.get("steps", []):
            tool = step.get("tool", "")
            args = step.get("arguments", {})
            result = await self.call_tool(tool, args)
            results.append({"tool": tool, "arguments": args, "result": result})
        return results

    async def execute_edge_probes(self) -> list[dict[str, Any]]:
        probes = generate_edge_probes(self._tools)
        results = []
        for probe in probes:
            self._probe_context = probe["probe_type"]
            try:
                req = probe["request"]
                method = req.get("method")
                params = req.get("params", {})

                if method == "tools/call":
                    result = await self.call_tool(
                        params.get("name", ""), params.get("arguments", {})
                    )
                else:
                    result = await self.send_raw(req)
            finally:
                self._probe_context = None

            results.append(
                {
                    "probe_type": probe["probe_type"],
                    "description": probe["description"],
                    "result": result,
                }
            )
        return results

    async def send_raw(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        if msg.get("id") is None:
            self._request_id += 1
            msg["id"] = self._request_id

        rid = msg.get("id")
        self._record(msg, CLIENT_TO_SERVER)
        if rid is not None:
            future: asyncio.Future = asyncio.get_event_loop().create_future()
            self._pending[rid] = future
            self._send_times[rid] = time.monotonic()

        if self._process and self._process.stdin:
            self._process.stdin.write((json.dumps(msg) + "\n").encode())
            await self._process.stdin.drain()

        if rid is not None:
            try:
                return await asyncio.wait_for(future, timeout=10.0)
            except TimeoutError:
                self._pending.pop(rid, None)
                self._send_times.pop(rid, None)
                return None
        return None

    async def send_raw_bytes(self, payload: bytes) -> None:
        if self._process and self._process.stdin:
            self._process.stdin.write(payload)
            await self._process.stdin.drain()

    async def _send_request(self, method: str, params: dict[str, Any]) -> dict[str, Any] | None:
        self._request_id += 1
        rid = self._request_id
        msg = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}

        self._record(msg, CLIENT_TO_SERVER)
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[rid] = future
        self._send_times[rid] = time.monotonic()

        if self._process and self._process.stdin:
            self._process.stdin.write((json.dumps(msg) + "\n").encode())
            await self._process.stdin.drain()

        try:
            result = await asyncio.wait_for(future, timeout=30.0)
            return result
        except TimeoutError:
            self._pending.pop(rid, None)
            self._send_times.pop(rid, None)
            return None

    async def _send_notification(self, method: str, params: dict[str, Any] | None = None) -> None:
        msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params:
            msg["params"] = params

        self._record(msg, CLIENT_TO_SERVER)

        if self._process and self._process.stdin:
            self._process.stdin.write((json.dumps(msg) + "\n").encode())
            await self._process.stdin.drain()

    async def _respond_to_server_request(self, rid: int | str, method: str) -> None:
        if method == "roots/list":
            response = {"jsonrpc": "2.0", "id": rid, "result": {"roots": []}}
        elif method == "ping":
            response = {"jsonrpc": "2.0", "id": rid, "result": {}}
        else:
            response = {
                "jsonrpc": "2.0",
                "id": rid,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            }
        self._record(response, CLIENT_TO_SERVER)
        if self._process and self._process.stdin:
            try:
                self._process.stdin.write((json.dumps(response) + "\n").encode())
                await self._process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass

    async def _read_responses(self) -> None:
        if not self._process or not self._process.stdout:
            return
        try:
            while True:
                line = await self._process.stdout.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if not text:
                    continue
                try:
                    msg = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if not isinstance(msg, dict):
                    continue

                rid = msg.get("id")
                latency_ms = None
                if rid is not None and rid in self._send_times:
                    latency_ms = (time.monotonic() - self._send_times.pop(rid)) * 1000

                self._record(msg, SERVER_TO_CLIENT, latency_ms=latency_ms)

                # Server-originated request — must reply or it orphans.
                # `sampling/createMessage` is intentionally left orphaned by
                # the `sample_llm_trigger` scenario in everything.py.
                method = msg.get("method")
                if method and rid is not None and method != "sampling/createMessage":
                    await self._respond_to_server_request(rid, method)
                    continue

                if rid is not None and rid in self._pending:
                    future = self._pending.pop(rid)
                    if not future.done():
                        future.set_result(msg)
        except asyncio.CancelledError:
            pass

    def _record(
        self,
        raw: dict[str, Any],
        direction: str,
        latency_ms: float | None = None,
    ) -> None:
        has_method = "method" in raw
        has_id = "id" in raw and raw["id"] is not None
        has_result_or_error = "result" in raw or "error" in raw

        rid = raw.get("id")
        is_probe = False
        probe_type: str | None = None
        if direction == CLIENT_TO_SERVER and self._probe_context is not None:
            is_probe = True
            probe_type = self._probe_context
            if rid is not None:
                self._probe_rids[rid] = probe_type
        elif direction == SERVER_TO_CLIENT and rid is not None and rid in self._probe_rids:
            is_probe = True
            probe_type = self._probe_rids.pop(rid)

        event = McpEvent(
            event_id=str(uuid4()),
            timestamp=datetime.now(UTC),
            session_id=self._session_id,
            transport="stdio",
            direction=direction,
            jsonrpc_id=raw.get("id"),
            method=raw.get("method"),
            is_request=has_method and has_id,
            is_response=has_result_or_error and has_id,
            is_notification=has_method and not has_id,
            is_error="error" in raw or (isinstance(raw.get("result"), dict) and raw["result"].get("isError")),
            params=raw.get("params"),
            result=raw.get("result"),
            error=raw.get("error"),
            latency_ms=latency_ms,
            raw_size_bytes=len(json.dumps(raw).encode()),
            raw_message=raw,
            is_probe=is_probe,
            probe_type=probe_type,
        )
        self._events.append(event)
