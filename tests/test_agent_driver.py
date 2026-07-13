"""Tests for stdio agent-driver process boundaries."""

from __future__ import annotations

import asyncio
import sys

import pytest

from reviewmymcp.ingest.schema import Direction, ToolDefinition
from reviewmymcp.synthetic.agent_driver import StdioAgentDriver


@pytest.mark.asyncio
async def test_dead_server_resolves_pending_tool_call_promptly():
    driver = StdioAgentDriver([sys.executable, "-c", "import sys; sys.stdin.readline()"])
    await driver.start()

    try:
        result = await asyncio.wait_for(driver.call_tool("search", {}), timeout=0.5)
    finally:
        await driver.stop()

    assert result is None


@pytest.mark.asyncio
async def test_stop_waits_for_process_after_forced_kill(monkeypatch):
    class FakeProcess:
        stdin = None

        def __init__(self):
            self.killed = False
            self.wait_calls = 0

        async def wait(self):
            self.wait_calls += 1

        def kill(self):
            self.killed = True

    async def force_timeout(awaitable, timeout):
        awaitable.close()
        raise TimeoutError

    driver = StdioAgentDriver([])
    process = FakeProcess()
    driver._process = process
    monkeypatch.setattr("reviewmymcp.synthetic.agent_driver.asyncio.wait_for", force_timeout)

    await driver.stop()

    assert process.killed
    assert process.wait_calls == 1


@pytest.mark.asyncio
async def test_stress_calls_are_recorded_with_stress_tag():
    driver = StdioAgentDriver([])

    async def fake_call_tool(name: str, arguments: dict) -> dict:
        driver._record(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}},
            Direction.CLIENT_TO_SERVER,
        )
        driver._record({"jsonrpc": "2.0", "id": 1, "result": {}}, Direction.SERVER_TO_CLIENT)
        return {"result": {}}

    driver.call_tool = fake_call_tool

    await driver.execute_stress(
        [ToolDefinition(name="search", input_schema={"type": "object"})],
        calls_per_tool=2,
        duration_seconds=0,
    )

    assert len(driver.events) == 4
    assert all(event.is_stress for event in driver.events)
