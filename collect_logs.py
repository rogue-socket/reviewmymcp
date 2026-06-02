#!/usr/bin/env python3
"""
Drive real MCP servers over stdio, capture full JSON-RPC traffic as NDJSON.

Usage:
    python collect_logs.py [--output-dir logs/] [--skip everything] [--timeout 180]

Requires: node/npx, GITHUB_PERSONAL_ACCESS_TOKEN env var for the GitHub server.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from mcp_driver.driver import StdioAgentDriver
from mcp_driver.edge_probes import _default_value
from mcp_driver.schema import McpEvent
from scenarios import everything, github, playwright, web_search
from scenarios.filesystem import make_scenarios as make_filesystem_scenarios

SCRIPT_DIR = Path(__file__).parent


@dataclass
class ServerConfig:
    name: str
    command: list[str]
    scenarios: list[dict[str, Any]]
    output_file: Path
    env: dict[str, str] = field(default_factory=dict)
    setup: Callable[[], None] | None = None
    teardown: Callable[[], None] | None = None
    concurrent_burst: bool = True
    burst_sessions: int = 3
    burst_calls: int = 5


def build_server_configs(output_dir: Path, skip: set[str]) -> list[ServerConfig]:
    configs: list[ServerConfig] = []

    # --- Everything server ---
    if "everything" not in skip:
        configs.append(
            ServerConfig(
                name="everything",
                command=["npx", "-y", "@modelcontextprotocol/server-everything"],
                scenarios=everything.SCENARIOS,
                output_file=output_dir / "everything.ndjson",
            )
        )

    # --- Filesystem server ---
    if "filesystem" not in skip:
        fs_tmpdir = os.path.realpath(tempfile.mkdtemp(prefix="mcp_fs_"))

        configs.append(
            ServerConfig(
                name="filesystem",
                command=[
                    "npx", "-y", "@modelcontextprotocol/server-filesystem",
                    fs_tmpdir,
                ],
                scenarios=make_filesystem_scenarios(fs_tmpdir),
                output_file=output_dir / "filesystem.ndjson",
                teardown=lambda d=fs_tmpdir: shutil.rmtree(d, ignore_errors=True),
            )
        )

    # --- GitHub server ---
    if "github" not in skip:
        gh_token = os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN", "")
        if not gh_token:
            print("  [WARN] GITHUB_PERSONAL_ACCESS_TOKEN not set — skipping GitHub server")
        else:
            configs.append(
                ServerConfig(
                    name="github",
                    command=["npx", "-y", "@modelcontextprotocol/server-github"],
                    scenarios=github.SCENARIOS,
                    output_file=output_dir / "github.ndjson",
                    env={"GITHUB_PERSONAL_ACCESS_TOKEN": gh_token},
                    concurrent_burst=False,
                )
            )

    # --- Playwright server ---
    if "playwright" not in skip:
        configs.append(
            ServerConfig(
                name="playwright",
                command=["npx", "-y", "@playwright/mcp@latest"],
                scenarios=playwright.SCENARIOS,
                output_file=output_dir / "playwright.ndjson",
                concurrent_burst=False,
            )
        )

    # --- Web Search server ---
    if "web_search" not in skip:
        configs.append(
            ServerConfig(
                name="web_search",
                command=["npx", "-y", "@iflow-mcp/pskill9-web-search"],
                scenarios=web_search.SCENARIOS,
                output_file=output_dir / "web_search.ndjson",
            )
        )

    return configs


async def run_handshake(driver: StdioAgentDriver) -> dict[str, Any] | None:
    init_result = await driver.initialize()
    await driver.send_initialized()
    await driver.list_tools()
    return init_result


def scenario_tool_gaps(scenarios: list[dict[str, Any]], tool_names: set[str]) -> list[dict[str, str]]:
    gaps: list[dict[str, str]] = []
    for scenario in scenarios:
        scenario_name = scenario.get("name", "unnamed")
        for step in scenario.get("steps", []):
            tool = step.get("tool", "")
            if tool and tool not in tool_names:
                gaps.append({"scenario": scenario_name, "tool": tool})
    return gaps


def scenario_coverage_summary(scenarios: list[dict[str, Any]], events: list[McpEvent]) -> dict[str, Any]:
    expected_tools = [
        step["tool"]
        for scenario in scenarios
        for step in scenario.get("steps", [])
        if step.get("tool")
    ]
    observed_tools = [
        e.params.get("name")
        for e in events
        if e.is_request
        and e.method == "tools/call"
        and not e.is_probe
        and isinstance(e.params, dict)
        and e.params.get("name")
    ]

    expected_counts = Counter(expected_tools)
    observed_counts = Counter(observed_tools)
    missing_counts = {
        tool: expected_count - observed_counts.get(tool, 0)
        for tool, expected_count in expected_counts.items()
        if observed_counts.get(tool, 0) < expected_count
    }

    return {
        "expected_tool_call_steps": len(expected_tools),
        "observed_scenario_tool_calls": len(observed_tools),
        "expected_tools": sorted(expected_counts),
        "observed_tools": sorted(observed_counts),
        "missing_tool_call_counts": missing_counts,
    }


async def run_server_session(config: ServerConfig, timeout: int) -> list[McpEvent]:
    print(f"\n{'='*60}")
    print(f"  Server: {config.name}")
    print(f"  Command: {' '.join(config.command)}")
    print(f"{'='*60}")

    all_events: list[McpEvent] = []

    # --- Main session: scenarios + edge probes ---
    driver = StdioAgentDriver(
        server_command=config.command,
        session_id=f"main-{config.name}",
        env=config.env or None,
    )

    try:
        await driver.start()
        await asyncio.wait_for(run_handshake(driver), timeout=60)

        tool_count = len(driver.tools)
        print(f"  Handshake OK — {tool_count} tools discovered")
        gaps = scenario_tool_gaps(config.scenarios, {tool.name for tool in driver.tools})
        if gaps:
            print(f"  [WARN] {len(gaps)} scenario step(s) reference missing tools:")
            for gap in gaps[:10]:
                print(f"    - {gap['scenario']}: {gap['tool']}")

        for scenario in config.scenarios:
            name = scenario.get("name", "unnamed")
            print(f"  Running scenario: {name}")
            try:
                await asyncio.wait_for(
                    driver.execute_scenario(scenario),
                    timeout=timeout,
                )
            except TimeoutError:
                print(f"    [TIMEOUT] Scenario {name} timed out after {timeout}s")
            except Exception as e:
                print(f"    [ERROR] Scenario {name}: {e}")

        print("  Running edge probes...")
        try:
            await asyncio.wait_for(driver.execute_edge_probes(), timeout=timeout)
        except TimeoutError:
            print("    [TIMEOUT] Edge probes timed out")
        except Exception as e:
            print(f"    [ERROR] Edge probes: {e}")

        all_events.extend(driver.events)
    except TimeoutError:
        print(f"  [ERROR] Handshake timed out for {config.name}")
        all_events.extend(driver.events)
    except Exception as e:
        print(f"  [ERROR] Session failed for {config.name}: {e}")
        all_events.extend(driver.events)
    finally:
        await driver.stop()

    # --- Concurrent burst sessions ---
    if config.concurrent_burst and driver.tools:
        print(f"  Running concurrent burst ({config.burst_sessions} sessions)...")
        try:
            burst_events = await asyncio.wait_for(
                run_concurrent_burst(
                    config.command,
                    driver.tools,
                    config.burst_sessions,
                    config.burst_calls,
                    config.name,
                    config.env,
                ),
                timeout=timeout * 2,
            )
            all_events.extend(burst_events)
            print(f"    Burst captured {len(burst_events)} events")
        except TimeoutError:
            print("    [TIMEOUT] Concurrent burst timed out")
        except Exception as e:
            print(f"    [ERROR] Concurrent burst: {e}")

    return all_events


async def run_concurrent_burst(
    command: list[str],
    tools: list,
    n_sessions: int,
    calls_per_session: int,
    server_name: str,
    env: dict[str, str] | None,
) -> list[McpEvent]:

    async def single_burst_session(session_idx: int) -> list[McpEvent]:
        driver = StdioAgentDriver(
            server_command=command,
            session_id=f"burst-{server_name}-{session_idx}",
            env=env,
        )
        try:
            await driver.start()
            await run_handshake(driver)

            driver._probe_context = "concurrent_burst"
            try:
                for i in range(calls_per_session):
                    tool = tools[i % len(tools)]
                    props = tool.input_schema.get("properties", {})
                    required = tool.input_schema.get("required", [])
                    args = {r: _default_value(props.get(r, {})) for r in required}
                    try:
                        await asyncio.wait_for(
                            driver.call_tool(tool.name, args),
                            timeout=15,
                        )
                    except (TimeoutError, Exception):
                        pass
            finally:
                driver._probe_context = None

            return driver.events
        finally:
            await driver.stop()

    tasks = [single_burst_session(i) for i in range(n_sessions)]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_events = []
    for r in results:
        if isinstance(r, list):
            all_events.extend(r)
    return all_events


def write_ndjson(events: list[McpEvent], path: Path) -> None:
    events_sorted = sorted(events, key=lambda e: e.timestamp)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for event in events_sorted:
            f.write(event.to_ndjson_line() + "\n")


def ensure_playwright_browsers() -> None:
    try:
        subprocess.run(
            ["npx", "playwright", "install", "chromium"],
            capture_output=True,
            timeout=120,
        )
        print("  Playwright chromium browser ready")
    except Exception as e:
        print(f"  [WARN] Could not install Playwright browsers: {e}")


async def main(output_dir: str, skip: list[str] | list[list[str]], timeout: int) -> None:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    skip_values = [item for group in skip for item in (group if isinstance(group, list) else [group])]
    skip_set = {s.lower().strip() for s in skip_values}

    print("MCP Log Collector")
    print(f"Output: {out.resolve()}")
    print(f"Skip: {skip_set or 'none'}")
    print()

    # Pre-check: npx available
    if not shutil.which("npx"):
        print("[FATAL] npx not found — install Node.js first")
        sys.exit(1)

    # Install Playwright browsers if needed
    if "playwright" not in skip_set:
        print("Ensuring Playwright browsers are installed...")
        ensure_playwright_browsers()

    configs = build_server_configs(out, skip_set)

    if not configs:
        print("No servers to run (all skipped).")
        return

    total_events = 0
    start = time.monotonic()

    for config in configs:
        try:
            events = await run_server_session(config, timeout)
            write_ndjson(events, config.output_file)

            error_count = sum(1 for e in events if e.is_error)
            coverage = scenario_coverage_summary(config.scenarios, events)
            print(f"  => Wrote {len(events)} events ({error_count} errors) to {config.output_file.name}")
            print(
                "  => Scenario coverage: "
                f"{coverage['observed_scenario_tool_calls']}/{coverage['expected_tool_call_steps']} calls, "
                f"missing={coverage['missing_tool_call_counts']}"
            )
            total_events += len(events)
        except Exception as e:
            print(f"  [FATAL] {config.name} failed completely: {e}")
        finally:
            if config.teardown:
                config.teardown()

    elapsed = time.monotonic() - start
    print(f"\nDone. {total_events} total events across {len(configs)} servers in {elapsed:.1f}s")
    print(f"Logs written to: {out.resolve()}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Collect MCP server traffic logs")
    parser.add_argument(
        "--output-dir",
        default=str(SCRIPT_DIR / "logs"),
        help="Directory for output NDJSON files (default: ./logs/)",
    )
    parser.add_argument(
        "--skip",
        nargs="*",
        action="append",
        default=[],
        help="Server names to skip (everything, filesystem, github, playwright, web_search)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=180,
        help="Timeout per scenario/probe phase in seconds (default: 180)",
    )
    args = parser.parse_args()
    asyncio.run(main(args.output_dir, args.skip, args.timeout))
