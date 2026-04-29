"""Rich terminal report output."""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from reviewmymcp.evaluators.base import Severity
from reviewmymcp.scoring.grader import AuditReport, DimensionScore, Grade

GRADE_COLORS = {
    Grade.A: "green",
    Grade.B: "blue",
    Grade.C: "yellow",
    Grade.D: "red",
    Grade.F: "bold red",
}

SEVERITY_COLORS = {
    Severity.CRITICAL: "bold red",
    Severity.HIGH: "red",
    Severity.MEDIUM: "yellow",
    Severity.LOW: "cyan",
    Severity.INFO: "dim",
}


def render_report(report: AuditReport, console: Console | None = None) -> None:
    console = console or Console()

    header = Text()
    header.append("MCP Server Audit Report\n", style="bold")
    header.append(f"Server: {report.server_meta.server_name} v{report.server_meta.server_version}\n")
    header.append(f"Protocol: {report.server_meta.protocol_version}\n")
    header.append(f"Date: {report.timestamp.strftime('%Y-%m-%d %H:%M UTC')}\n")
    header.append(f"Events: {report.total_events}  Sessions: {report.total_sessions}")
    if report.tool_count:
        header.append(f"  Tools: {report.tool_count}")
    if report.call_count:
        header.append(f"  Calls: {report.call_count}")

    console.print(Panel(header, title="reviewmymcp", border_style="blue"))

    # Dimension scores table
    dim_table = Table(title="Dimension Scores", show_header=True)
    dim_table.add_column("Dimension", style="bold")
    dim_table.add_column("Score", justify="right")
    dim_table.add_column("Grade", justify="center")
    dim_table.add_column("Critical", justify="right")
    dim_table.add_column("High", justify="right")
    dim_table.add_column("Medium", justify="right")
    dim_table.add_column("Low", justify="right")
    dim_table.add_column("Skipped", justify="right")

    for ds in sorted(report.dimension_scores, key=lambda d: d.dimension):
        grade_style = GRADE_COLORS.get(ds.grade, "white")
        skipped = str(len(ds.checks_skipped)) if ds.checks_skipped else "-"
        dim_table.add_row(
            ds.dimension,
            f"{ds.score:.0f}",
            Text(ds.grade.value, style=grade_style),
            str(ds.critical_count) if ds.critical_count else "-",
            str(ds.high_count) if ds.high_count else "-",
            str(ds.medium_count) if ds.medium_count else "-",
            str(ds.low_count) if ds.low_count else "-",
            skipped,
        )

    console.print(dim_table)

    # Skipped checks detail
    all_skipped = [(ds.dimension, sc) for ds in report.dimension_scores for sc in ds.checks_skipped]
    if all_skipped:
        console.print("\n[bold]Skipped Checks[/bold] [dim](insufficient data)[/dim]")
        for dim, sc in all_skipped:
            console.print(f"  [dim]{sc.check_id}[/dim]: {sc.reason}")

    # Top findings
    if report.top_findings:
        console.print("\n[bold]Top Findings[/bold]")
        for i, finding in enumerate(report.top_findings, 1):
            sev_style = SEVERITY_COLORS.get(finding.severity, "white")
            console.print(f"\n  {i}. [{sev_style}][{finding.severity.value.upper()}][/{sev_style}] {finding.check_id}")
            console.print(f"     {finding.title}")
            if finding.remediation:
                console.print(f"     [dim]Fix: {finding.remediation}[/dim]")

    console.print()
