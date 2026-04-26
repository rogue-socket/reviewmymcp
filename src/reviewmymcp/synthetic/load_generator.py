"""Load generator — runs concurrent sessions for Performance Under Load evaluation."""

from __future__ import annotations

import asyncio
from typing import Any

from reviewmymcp.ingest.schema import McpEvent, ToolDefinition
from reviewmymcp.synthetic.agent_driver import StdioAgentDriver
from reviewmymcp.synthetic.edge_probes import _default_value


class LoadGenerator:
    """Runs concurrent synthetic sessions to test server behavior under load."""

    def __init__(
        self,
        server_command: list[str],
        redact: bool = True,
    ):
        self._server_command = server_command
        self._redact = redact
        self._all_events: list[McpEvent] = []

    @property
    def events(self) -> list[McpEvent]:
        return list(self._all_events)

    async def run_load_test(
        self,
        tools: list[ToolDefinition],
        concurrency_levels: list[int] | None = None,
        calls_per_session: int = 5,
    ) -> dict[str, Any]:
        if concurrency_levels is None:
            concurrency_levels = [1, 3, 5, 10]

        results: dict[str, Any] = {"levels": []}

        for level in concurrency_levels:
            level_result = await self._run_at_concurrency(tools, level, calls_per_session)
            results["levels"].append({"concurrency": level, **level_result})

        return results

    async def _run_at_concurrency(
        self,
        tools: list[ToolDefinition],
        concurrency: int,
        calls_per_session: int,
    ) -> dict[str, Any]:
        tasks = [self._run_single_session(tools, calls_per_session, session_idx=i) for i in range(concurrency)]
        session_results = await asyncio.gather(*tasks, return_exceptions=True)

        total_calls = 0
        total_errors = 0
        latencies: list[float] = []

        for result in session_results:
            if isinstance(result, Exception):
                total_errors += calls_per_session
                continue
            total_calls += result["calls"]
            total_errors += result["errors"]
            latencies.extend(result["latencies"])

        return {
            "total_calls": total_calls,
            "total_errors": total_errors,
            "error_rate": total_errors / max(total_calls, 1),
            "latencies": latencies,
            "latency_p50": _percentile(latencies, 50),
            "latency_p95": _percentile(latencies, 95),
            "latency_p99": _percentile(latencies, 99),
        }

    async def _run_single_session(
        self,
        tools: list[ToolDefinition],
        calls_per_session: int,
        session_idx: int,
    ) -> dict[str, Any]:
        driver = StdioAgentDriver(
            server_command=self._server_command,
            redact=self._redact,
        )

        try:
            await driver.start()
            await driver.initialize()
            await driver.send_initialized()
            await driver.list_tools()

            calls = 0
            errors = 0
            latencies: list[float] = []

            for i in range(calls_per_session):
                tool = tools[i % len(tools)] if tools else None
                if not tool:
                    break

                args = self._build_valid_args(tool)
                import time

                start = time.monotonic()
                result = await driver.call_tool(tool.name, args)
                elapsed_ms = (time.monotonic() - start) * 1000

                calls += 1
                latencies.append(elapsed_ms)

                if result is None:
                    errors += 1
                elif "error" in result or (isinstance(result.get("result"), dict) and result["result"].get("isError")):
                    errors += 1

            self._all_events.extend(driver.events)
            return {"calls": calls, "errors": errors, "latencies": latencies}

        finally:
            await driver.stop()

    def _build_valid_args(self, tool: ToolDefinition) -> dict[str, Any]:
        args: dict[str, Any] = {}
        required = tool.input_schema.get("required", [])
        properties = tool.input_schema.get("properties", {})
        for field in required:
            prop = properties.get(field, {})
            args[field] = _default_value(prop)
        return args


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    sorted_v = sorted(values)
    idx = int(len(sorted_v) * p / 100)
    return sorted_v[min(idx, len(sorted_v) - 1)]
