import asyncio
import sys

import pytest

from mcp_driver.driver import StdioAgentDriver
from mcp_driver.edge_probes import generate_edge_probes
from mcp_driver.schema import ToolDefinition


def test_edge_probes_do_not_fall_back_to_mutating_tools():
    probes = generate_edge_probes(
        [
            ToolDefinition(name="execute_command", input_schema={"type": "object"}),
            ToolDefinition(name="remove_file", input_schema={"type": "object"}),
        ]
    )

    probed_tools = {
        probe["request"].get("params", {}).get("name")
        for probe in probes
        if probe["request"].get("method") == "tools/call"
    }
    assert not {"execute_command", "remove_file"} & probed_tools


@pytest.mark.asyncio
async def test_server_exit_resolves_pending_call_promptly():
    driver = StdioAgentDriver(
        [sys.executable, "-c", "import sys; sys.stdin.readline()"]
    )
    await driver.start()

    try:
        result = await asyncio.wait_for(driver.call_tool("search", {}), timeout=0.5)
    finally:
        await driver.stop()

    assert result is None
