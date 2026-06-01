"""Runner — orchestrates the full active check pipeline.

Connects to an MCP server, generates tasks, runs an agent through them,
observes the results, and produces a graded report.
"""

from __future__ import annotations

from typing import Any

import click

from reviewmymcp.active.agent_loop import AgentLoop, TaskSession
from reviewmymcp.active.observer import SessionAnalysis, analyze_session
from reviewmymcp.active.scorer import compute_summary_stats, score_sessions
from reviewmymcp.active.task_library import TaskLibrary
from reviewmymcp.ingest.schema import ServerMeta, ToolDefinition
from reviewmymcp.judge.base import JudgeProvider
from reviewmymcp.scoring.grader import AuditReport
from reviewmymcp.synthetic.agent_driver import StdioAgentDriver


class ActiveCheckResult:
    """Container for the full active check output."""

    def __init__(
        self,
        report: AuditReport,
        sessions: list[TaskSession],
        analyses: list[SessionAnalysis],
        summary: dict[str, Any],
        server_meta: ServerMeta,
    ):
        self.report = report
        self.sessions = sessions
        self.analyses = analyses
        self.summary = summary
        self.server_meta = server_meta


async def run_active_check(
    server_command: list[str],
    judge: JudgeProvider,
    task_count: int = 8,
    redact: bool = True,
) -> ActiveCheckResult:
    """Run the full active check pipeline against a stdio MCP server."""
    driver = StdioAgentDriver(server_command=server_command, redact=redact)

    try:
        await driver.start()
        click.echo("  Initializing server...", err=True)
        await driver.initialize()
        await driver.send_initialized()

        click.echo("  Discovering tools...", err=True)
        tools_result = await driver.list_tools()

        tools: list[ToolDefinition] = []
        server_meta = ServerMeta()

        if tools_result and "result" in tools_result:
            for t in tools_result["result"].get("tools", []):
                tools.append(ToolDefinition(
                    name=t.get("name", ""),
                    description=t.get("description", ""),
                    input_schema=t.get("inputSchema", {}),
                ))
        server_meta.tools = tools

        init_events = [e for e in driver.events if e.method == "initialize" and e.is_response]
        if init_events:
            resp = init_events[0]
            if resp.result:
                info = resp.result.get("serverInfo", {})
                server_meta.server_name = info.get("name", "")
                server_meta.server_version = info.get("version", "")
                server_meta.protocol_version = resp.result.get("protocolVersion", "")
                server_meta.instructions = resp.result.get("instructions", "")

        click.echo(f"  Found {len(tools)} tools. Generating test tasks...", err=True)

        library = TaskLibrary(judge=judge)
        tasks = await library.generate_tasks(
            tools,
            server_name=server_meta.server_name,
            server_instructions=server_meta.instructions,
            count=task_count,
        )
        click.echo(f"  Generated {len(tasks)} tasks. Running agent sessions...", err=True)

        sessions: list[TaskSession] = []
        analyses: list[SessionAnalysis] = []

        for i, task in enumerate(tasks, 1):
            click.echo(f"  [{i}/{len(tasks)}] {task.category.value}: {task.instruction[:60]}...", err=True)

            loop = AgentLoop(
                judge=judge,
                call_tool_fn=driver.call_tool,
                tools=tools,
            )
            session = await loop.run_task(task)
            sessions.append(session)

            analysis = analyze_session(session)
            analyses.append(analysis)

            status = "completed" if analysis.success else "failed"
            click.echo(f"         -> {status} in {analysis.turns_used} turns", err=True)

        click.echo("  Scoring results...", err=True)
        summary = compute_summary_stats(analyses)
        report = score_sessions(analyses, server_meta)

        click.echo(
            f"  Done. {summary['completed']}/{summary['total_tasks']} tasks completed. "
            f"Grade: {report.overall_grade.value}",
            err=True,
        )

        return ActiveCheckResult(
            report=report,
            sessions=sessions,
            analyses=analyses,
            summary=summary,
            server_meta=server_meta,
        )

    finally:
        await driver.stop()
