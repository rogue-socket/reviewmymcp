"""Stdio transparent proxy — sits between MCP client and server, captures all traffic."""

from __future__ import annotations

import asyncio
import json
import signal
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from reviewmymcp.ingest.normalizer import normalize_event
from reviewmymcp.ingest.redactor import redact_dict
from reviewmymcp.ingest.schema import Direction, McpEvent, Transport


class StdioProxy:
    """Transparent stdio proxy that captures MCP JSON-RPC traffic between client and server."""

    def __init__(
        self,
        server_command: list[str],
        log_file: str | Path | None = None,
        session_id: str | None = None,
        redact: bool = True,
        extra_redaction_patterns: list[str] | None = None,
    ):
        self._server_command = server_command
        self._log_file = Path(log_file) if log_file else None
        self._session_id = session_id or str(uuid4())
        self._redact = redact
        self._extra_patterns = extra_redaction_patterns or []
        self._events: list[McpEvent] = []
        self._process: asyncio.subprocess.Process | None = None
        self._log_handle = None

    @property
    def events(self) -> list[McpEvent]:
        return list(self._events)

    async def run(self) -> int:
        if self._log_file:
            self._log_handle = open(self._log_file, "a", encoding="utf-8")

        self._process = await asyncio.create_subprocess_exec(
            *self._server_command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda: asyncio.ensure_future(self._shutdown()))

        tasks = [
            asyncio.create_task(self._forward_client_to_server()),
            asyncio.create_task(self._forward_server_to_client()),
            asyncio.create_task(self._forward_stderr()),
        ]

        await self._process.wait()

        for task in tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        if self._log_handle:
            self._log_handle.close()

        return self._process.returncode or 0

    async def _forward_client_to_server(self) -> None:
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await asyncio.get_event_loop().connect_read_pipe(lambda: protocol, sys.stdin)

        try:
            while True:
                line = await reader.readline()
                if not line:
                    if self._process and self._process.stdin:
                        self._process.stdin.close()
                    break
                self._capture(line, Direction.CLIENT_TO_SERVER)
                if self._process and self._process.stdin:
                    self._process.stdin.write(line)
                    await self._process.stdin.drain()
        except (asyncio.CancelledError, ConnectionError):
            pass

    async def _forward_server_to_client(self) -> None:
        if not self._process or not self._process.stdout:
            return
        try:
            while True:
                line = await self._process.stdout.readline()
                if not line:
                    break
                self._capture(line, Direction.SERVER_TO_CLIENT)
                sys.stdout.buffer.write(line)
                sys.stdout.buffer.flush()
        except (asyncio.CancelledError, ConnectionError):
            pass

    async def _forward_stderr(self) -> None:
        if not self._process or not self._process.stderr:
            return
        try:
            while True:
                line = await self._process.stderr.readline()
                if not line:
                    break
                sys.stderr.buffer.write(line)
                sys.stderr.buffer.flush()
        except (asyncio.CancelledError, ConnectionError):
            pass

    def _capture(self, line: bytes, direction: Direction) -> None:
        text = line.decode("utf-8", errors="replace").strip()
        if not text:
            return

        try:
            raw = json.loads(text)
        except json.JSONDecodeError:
            return

        if not isinstance(raw, dict):
            return

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

        if self._log_handle:
            log_record = {
                "jsonrpc": raw.get("jsonrpc"),
                "direction": direction.value,
                "timestamp": event.timestamp.isoformat(),
                "session_id": self._session_id,
                **{k: v for k, v in raw.items() if k != "jsonrpc"},
            }
            self._log_handle.write(json.dumps(log_record) + "\n")
            self._log_handle.flush()

    async def _shutdown(self) -> None:
        if self._process and self._process.returncode is None:
            if self._process.stdin:
                self._process.stdin.close()
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except TimeoutError:
                self._process.kill()


async def run_stdio_proxy(
    server_command: list[str],
    log_file: str | Path | None = None,
    redact: bool = True,
) -> tuple[int, list[McpEvent]]:
    proxy = StdioProxy(server_command=server_command, log_file=log_file, redact=redact)
    exit_code = await proxy.run()
    return exit_code, proxy.events
