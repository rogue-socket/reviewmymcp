"""Agent driver — executes scenarios against a live MCP server via JSON-RPC."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from reviewmymcp.ingest.normalizer import normalize_event
from reviewmymcp.ingest.redactor import redact_dict
from reviewmymcp.ingest.schema import Direction, McpEvent, Transport
from reviewmymcp.synthetic.edge_probes import generate_edge_probes


class StdioAgentDriver:
    """Drives an MCP server over stdio, executing scenarios and edge probes."""

    def __init__(
        self,
        server_command: list[str],
        redact: bool = True,
        extra_redaction_patterns: list[str] | None = None,
    ):
        self._server_command = server_command
        self._redact = redact
        self._extra_patterns = extra_redaction_patterns or []
        self._events: list[McpEvent] = []
        self._process: asyncio.subprocess.Process | None = None
        self._request_id = 0
        self._session_id = str(uuid4())
        self._pending: dict[int, asyncio.Future] = {}

    @property
    def events(self) -> list[McpEvent]:
        return list(self._events)

    async def start(self) -> None:
        self._process = await asyncio.create_subprocess_exec(
            *self._server_command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
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
                "clientInfo": {"name": "reviewmymcp-synthetic", "version": "0.1.0"},
            },
        )

    async def send_initialized(self) -> None:
        await self._send_notification("notifications/initialized")

    async def list_tools(self) -> dict[str, Any] | None:
        return await self._send_request("tools/list", {})

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

    async def execute_edge_probes(self, tools: list[Any]) -> list[dict[str, Any]]:
        from reviewmymcp.ingest.schema import ToolDefinition

        tool_defs = []
        for t in tools:
            if isinstance(t, ToolDefinition):
                tool_defs.append(t)
            elif isinstance(t, dict):
                tool_defs.append(ToolDefinition(**t))

        probes = generate_edge_probes(tool_defs)
        results = []

        for probe in probes:
            req = probe["request"]
            method = req.get("method")
            params = req.get("params", {})

            if method == "tools/call":
                result = await self.call_tool(params.get("name", ""), params.get("arguments", {}))
            else:
                result = await self._send_raw(req)

            results.append(
                {
                    "probe_type": probe["probe_type"],
                    "description": probe["description"],
                    "result": result,
                }
            )

        return results

    async def _send_request(self, method: str, params: dict[str, Any]) -> dict[str, Any] | None:
        self._request_id += 1
        rid = self._request_id
        msg = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}

        self._record(msg, Direction.CLIENT_TO_SERVER)

        if self._process and self._process.stdin:
            self._process.stdin.write((json.dumps(msg) + "\n").encode())
            await self._process.stdin.drain()

        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[rid] = future

        try:
            result = await asyncio.wait_for(future, timeout=30.0)
            return result
        except TimeoutError:
            self._pending.pop(rid, None)
            return None

    async def _send_notification(self, method: str, params: dict[str, Any] | None = None) -> None:
        msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params:
            msg["params"] = params

        self._record(msg, Direction.CLIENT_TO_SERVER)

        if self._process and self._process.stdin:
            self._process.stdin.write((json.dumps(msg) + "\n").encode())
            await self._process.stdin.drain()

    async def _send_raw(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        if msg.get("id") is None:
            self._request_id += 1
            msg["id"] = self._request_id

        rid = msg.get("id")
        self._record(msg, Direction.CLIENT_TO_SERVER)

        if self._process and self._process.stdin:
            self._process.stdin.write((json.dumps(msg) + "\n").encode())
            await self._process.stdin.drain()

        if rid is not None:
            future: asyncio.Future = asyncio.get_event_loop().create_future()
            self._pending[rid] = future
            try:
                return await asyncio.wait_for(future, timeout=10.0)
            except TimeoutError:
                self._pending.pop(rid, None)
                return None
        return None

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

                self._record(msg, Direction.SERVER_TO_CLIENT)

                rid = msg.get("id")
                if rid is not None and rid in self._pending:
                    future = self._pending.pop(rid)
                    if not future.done():
                        future.set_result(msg)
        except asyncio.CancelledError:
            pass

    def _record(self, raw: dict[str, Any], direction: Direction) -> None:
        if self._redact:
            raw, redacted_fields = redact_dict(raw, extra_patterns=self._extra_patterns)
        else:
            redacted_fields = []

        event = normalize_event(
            raw=raw,
            transport=Transport.STDIO,
            direction=direction,
            session_id=self._session_id,
            timestamp=datetime.now(UTC),
        )
        event.redacted_fields = redacted_fields
        self._events.append(event)
