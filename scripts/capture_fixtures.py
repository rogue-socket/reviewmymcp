#!/usr/bin/env python3
"""Capture NDJSON fixture files from real MCP servers for integration testing."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from reviewmymcp.synthetic.agent_driver import StdioAgentDriver
from reviewmymcp.ingest.schema import ToolDefinition


async def capture_server(name: str, command: list[str], output_path: Path) -> None:
    print(f"\n{'='*60}")
    print(f"Capturing: {name}")
    print(f"Command: {' '.join(command)}")
    print(f"Output: {output_path}")
    print(f"{'='*60}")

    driver = StdioAgentDriver(server_command=command, redact=False)

    try:
        await driver.start()

        # Initialize
        print("  Initializing...")
        init_result = await driver.initialize()
        if not init_result:
            print("  ERROR: Initialize failed")
            return
        await driver.send_initialized()

        # List tools
        print("  Listing tools...")
        tools_result = await driver.list_tools()
        tools: list[ToolDefinition] = []
        if tools_result and "result" in tools_result:
            for t in tools_result["result"].get("tools", []):
                tools.append(
                    ToolDefinition(
                        name=t.get("name", ""),
                        description=t.get("description", ""),
                        input_schema=t.get("inputSchema", {}),
                    )
                )
        print(f"  Found {len(tools)} tools: {[t.name for t in tools]}")

        # Call each tool with basic arguments
        for tool in tools:
            required = tool.input_schema.get("required", [])
            properties = tool.input_schema.get("properties", {})
            args = {}
            for field in required:
                prop = properties.get(field, {})
                t = prop.get("type", "string")
                if t == "string":
                    args[field] = f"test_{field}"
                elif t == "integer":
                    args[field] = 1
                elif t == "number":
                    args[field] = 1.0
                elif t == "boolean":
                    args[field] = True
                elif t == "object":
                    args[field] = {}
                elif t == "array":
                    args[field] = []
                else:
                    args[field] = f"test_{field}"

            print(f"  Calling {tool.name}({args})...")
            try:
                await driver.call_tool(tool.name, args)
            except Exception as e:
                print(f"    Error: {e}")

        # Call a few tools again for repeat/reliability data
        if tools:
            for tool in tools[:3]:
                required = tool.input_schema.get("required", [])
                properties = tool.input_schema.get("properties", {})
                args = {}
                for field in required:
                    prop = properties.get(field, {})
                    t = prop.get("type", "string")
                    if t == "string":
                        args[field] = f"second_test_{field}"
                    elif t == "integer":
                        args[field] = 2
                    else:
                        args[field] = f"test2_{field}"

                print(f"  Re-calling {tool.name}({args})...")
                try:
                    await driver.call_tool(tool.name, args)
                except Exception:
                    pass

        # Try an invalid tool call for error data
        print("  Calling nonexistent tool...")
        try:
            await driver.call_tool("__nonexistent_tool__", {})
        except Exception:
            pass

        # Try a tool with wrong argument types
        if tools:
            print(f"  Calling {tools[0].name} with wrong args...")
            try:
                await driver.call_tool(tools[0].name, {"__invalid__": "bad"})
            except Exception:
                pass

        events = driver.events
        print(f"  Captured {len(events)} events")

        # Write NDJSON
        with open(output_path, "w", encoding="utf-8") as f:
            for event in events:
                record = {
                    "direction": event.direction.value,
                    "timestamp": event.timestamp.isoformat(),
                    "session_id": event.session_id,
                }
                if event.raw_message:
                    record.update(event.raw_message)
                else:
                    # Reconstruct from event fields
                    msg: dict = {"jsonrpc": "2.0"}
                    if event.jsonrpc_id is not None:
                        msg["id"] = event.jsonrpc_id
                    if event.method:
                        msg["method"] = event.method
                    if event.params:
                        msg["params"] = event.params
                    if event.result:
                        msg["result"] = event.result
                    if event.error:
                        msg["error"] = event.error
                    record.update(msg)
                f.write(json.dumps(record) + "\n")

        print(f"  Wrote {output_path}")

    finally:
        await driver.stop()


async def main():
    fixtures_dir = Path(__file__).parent.parent / "tests" / "fixtures"

    servers = [
        (
            "Everything Server",
            ["npx", "-y", "@modelcontextprotocol/server-everything"],
            fixtures_dir / "everything_server.ndjson",
        ),
        (
            "Filesystem Server",
            ["npx", "-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
            fixtures_dir / "filesystem_server.ndjson",
        ),
    ]

    for name, command, output_path in servers:
        try:
            await capture_server(name, command, output_path)
        except Exception as e:
            print(f"  FAILED: {e}")


if __name__ == "__main__":
    asyncio.run(main())
