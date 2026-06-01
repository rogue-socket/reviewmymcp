"""CLI command for active MCP checking."""

from __future__ import annotations

import sys

import click


@click.command("active-audit")
@click.argument("target")
@click.option(
    "--output",
    "output_format",
    type=click.Choice(["terminal", "json", "html", "sarif"]),
    default="terminal",
)
@click.option("--output-file", type=click.Path(), default=None)
@click.option("--task-count", type=int, default=8, help="Number of test tasks to generate")
@click.option("--no-redact", is_flag=True, default=False)
@click.option("--judge-provider", type=click.Choice(["anthropic", "gemini", "openai"]), default="anthropic")
@click.option("--judge-model", type=str, default="", help="Model for both the judge and test agent")
@click.option("--config", "config_path", type=click.Path(exists=True), default=None)
def active_audit(
    target: str,
    output_format: str,
    output_file: str | None,
    task_count: int,
    no_redact: bool,
    judge_provider: str,
    judge_model: str,
    config_path: str | None,
) -> None:
    """Run an active (agent-driven) audit against a live MCP server.

    Unlike the passive audit which analyzes logs, this mode puts an LLM agent
    in front of the MCP server, gives it tasks, and observes how well the
    server supports the agent's workflow. Think of it as a usability test.

    TARGET is the server command to run (e.g., "python my_server.py").
    """
    import asyncio

    from reviewmymcp.config import AuditConfig

    if config_path:
        audit_config = AuditConfig.from_file(config_path)
    else:
        audit_config = AuditConfig.default()

    audit_config.judge.provider = judge_provider
    if judge_model:
        audit_config.judge.model = judge_model

    if target.startswith("http"):
        click.echo("Error: active-audit currently supports stdio servers only. Pass a server command.", err=True)
        sys.exit(2)

    judge = _create_judge(audit_config)
    if not judge:
        click.echo("Error: active-audit requires an LLM judge. Set ANTHROPIC_API_KEY (or equivalent).", err=True)
        sys.exit(2)

    server_command = target.split()

    click.echo(f"Starting active audit against: {target}", err=True)
    click.echo("This mode runs an LLM agent against the MCP server to test usability.", err=True)

    from reviewmymcp.active.runner import run_active_check

    result = asyncio.run(run_active_check(
        server_command=server_command,
        judge=judge,
        task_count=task_count,
        redact=not no_redact,
    ))

    _output_active_report(result, output_format, output_file)

    has_serious = any(f.severity.value in ("critical", "high") for f in result.report.top_findings)
    sys.exit(1 if has_serious else 0)


def _create_judge(audit_config):
    provider = audit_config.judge.provider
    model = audit_config.judge.model

    if provider == "anthropic":
        from reviewmymcp.judge.anthropic_judge import AnthropicJudge
        return AnthropicJudge(model=model)
    elif provider == "gemini":
        from reviewmymcp.judge.gemini_judge import GeminiJudge
        return GeminiJudge(model=model)
    elif provider == "openai":
        from reviewmymcp.judge.openai_judge import OpenAIJudge
        return OpenAIJudge(model=model)
    return None


def _output_active_report(result, output_format: str, output_file: str | None) -> None:
    report = result.report

    if output_format == "terminal":
        _render_active_terminal(result)
    elif output_format == "json":
        from reviewmymcp.reporting.json_report import render_json, write_json
        if output_file:
            write_json(report, output_file)
        else:
            click.echo(render_json(report))
    elif output_format == "html":
        from reviewmymcp.reporting.html_report import render_html, write_html
        if output_file:
            write_html(report, output_file)
        else:
            click.echo(render_html(report))
    elif output_format == "sarif":
        from reviewmymcp.reporting.sarif_report import render_sarif, write_sarif
        if output_file:
            write_sarif(report, output_file)
        else:
            click.echo(render_sarif(report))


def _render_active_terminal(result) -> None:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    from reviewmymcp.evaluators.base import Severity
    from reviewmymcp.scoring.grader import Grade

    grade_colors = {
        Grade.A: "green", Grade.B: "blue", Grade.C: "yellow",
        Grade.D: "red", Grade.F: "bold red",
    }
    severity_colors = {
        Severity.CRITICAL: "bold red", Severity.HIGH: "red",
        Severity.MEDIUM: "yellow", Severity.LOW: "cyan", Severity.INFO: "dim",
    }

    console = Console()
    report = result.report
    summary = result.summary

    header = Text()
    header.append("Active MCP Audit Report\n", style="bold")
    header.append(f"Server: {report.server_meta.server_name} v{report.server_meta.server_version}\n")
    header.append(f"Protocol: {report.server_meta.protocol_version}\n")
    header.append("Mode: Agent-driven usability test\n")
    header.append(
        f"Tasks: {summary['completed']}/{summary['total_tasks']} completed "
        f"({summary['completion_rate']:.0%})\n"
    )
    header.append(f"Avg turns per task: {summary['avg_turns_per_task']}\n")

    grade_text = Text(
        f"  {report.overall_grade.value}  ",
        style=f"bold {grade_colors.get(report.overall_grade, 'white')} on black",
    )
    header.append("\nOverall Grade: ")
    header.append(grade_text)

    console.print(Panel(header, title="reviewmymcp active-audit", border_style="magenta"))

    cat_table = Table(title="Results by Task Category", show_header=True)
    cat_table.add_column("Category", style="bold")
    cat_table.add_column("Completed", justify="center")
    cat_table.add_column("Total", justify="center")
    cat_table.add_column("Rate", justify="center")

    for cat, stats in sorted(summary.get("by_category", {}).items()):
        rate = stats["success"] / stats["total"] if stats["total"] else 0
        color = "green" if rate >= 0.8 else "yellow" if rate >= 0.5 else "red"
        cat_table.add_row(
            cat,
            str(stats["success"]),
            str(stats["total"]),
            Text(f"{rate:.0%}", style=color),
        )

    console.print(cat_table)

    dim_table = Table(title="Dimension Scores", show_header=True)
    dim_table.add_column("Dimension", style="bold")
    dim_table.add_column("Grade", justify="center")
    dim_table.add_column("Critical", justify="right")
    dim_table.add_column("High", justify="right")
    dim_table.add_column("Medium", justify="right")
    dim_table.add_column("Low", justify="right")

    for ds in sorted(report.dimension_scores, key=lambda d: d.dimension):
        grade_style = grade_colors.get(ds.grade, "white")
        dim_table.add_row(
            ds.dimension,
            Text(ds.grade.value, style=grade_style),
            str(ds.critical_count) if ds.critical_count else "-",
            str(ds.high_count) if ds.high_count else "-",
            str(ds.medium_count) if ds.medium_count else "-",
            str(ds.low_count) if ds.low_count else "-",
        )

    console.print(dim_table)

    if report.top_findings:
        console.print("\n[bold]Top Findings[/bold]")
        for i, finding in enumerate(report.top_findings, 1):
            sev_style = severity_colors.get(finding.severity, "white")
            console.print(
                f"\n  {i}. [{sev_style}][{finding.severity.value.upper()}][/{sev_style}] {finding.check_id}"
            )
            console.print(f"     {finding.title}")
            if finding.remediation:
                console.print(f"     [dim]Fix: {finding.remediation}[/dim]")

    console.print()

    if summary.get("positive_signals", 0) + summary.get("negative_signals", 0) > 0:
        pos = summary["positive_signals"]
        neg = summary["negative_signals"]
        total_sig = pos + neg
        console.print(
            f"[dim]Signals: {pos} positive, {neg} negative "
            f"({pos / total_sig:.0%} positive rate)[/dim]\n"
        )
