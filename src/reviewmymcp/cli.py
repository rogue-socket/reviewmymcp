"""CLI entry points for reviewmymcp."""

from __future__ import annotations

import sys
from pathlib import Path

import click
from rich.console import Console

from reviewmymcp import __version__


def _register_evaluators() -> None:
    """Import and register all evaluator modules."""
    from reviewmymcp.evaluators.accuracy.checks import AccuracyEvaluator
    from reviewmymcp.evaluators.compliance.checks import ComplianceEvaluator
    from reviewmymcp.evaluators.composability.checks import ComposabilityEvaluator
    from reviewmymcp.evaluators.conformance.checks import ConformanceEvaluator
    from reviewmymcp.evaluators.discoverability.checks import DiscoverabilityEvaluator
    from reviewmymcp.evaluators.efficiency.checks import EfficiencyEvaluator
    from reviewmymcp.evaluators.performance.checks import PerformanceEvaluator
    from reviewmymcp.evaluators.registry import register
    from reviewmymcp.evaluators.reliability.checks import ReliabilityEvaluator
    from reviewmymcp.evaluators.security.checks import SecurityEvaluator

    for cls in [
        EfficiencyEvaluator,
        AccuracyEvaluator,
        DiscoverabilityEvaluator,
        ComposabilityEvaluator,
        ReliabilityEvaluator,
        SecurityEvaluator,
        ComplianceEvaluator,
        ConformanceEvaluator,
        PerformanceEvaluator,
    ]:
        try:
            register(cls())
        except ValueError:
            pass  # already registered


@click.group()
@click.version_option(version=__version__)
def cli() -> None:
    """reviewmymcp — Log-driven MCP server audit and evaluation tool."""
    pass


@cli.command()
@click.argument("target", required=False)
@click.option("--log-file", type=click.Path(exists=True), help="Use captured logs instead of live traffic")
@click.option("--transport", type=click.Choice(["stdio", "http"]), default=None)
@click.option(
    "--output",
    "output_format",
    type=click.Choice(["terminal", "json", "html", "sarif"]),
    default="terminal",
)
@click.option("--output-file", type=click.Path(), default=None)
@click.option("--dimensions", type=str, default=None, help="Comma-separated dimensions to run")
@click.option("--severity", type=click.Choice(["critical", "high", "medium", "low", "info"]), default="info")
@click.option("--config", "config_path", type=click.Path(exists=True), default=None)
@click.option("--no-redact", is_flag=True, default=False)
@click.option("--no-llm-judges", is_flag=True, default=False)
@click.option("--judge-provider", type=click.Choice(["anthropic", "gemini", "openai"]), default="anthropic")
@click.option("--judge-model", type=str, default="")
def audit(
    target: str | None,
    log_file: str | None,
    transport: str | None,
    output_format: str,
    output_file: str | None,
    dimensions: str | None,
    severity: str,
    config_path: str | None,
    no_redact: bool,
    no_llm_judges: bool,
    judge_provider: str,
    judge_model: str,
) -> None:
    """Run a full audit on an MCP server or log file."""
    from reviewmymcp.config import AuditConfig
    from reviewmymcp.evaluators.base import EvaluatorConfig
    from reviewmymcp.evaluators.registry import run_all
    from reviewmymcp.ingest.correlator import correlate, get_sessions
    from reviewmymcp.ingest.file_loader import load_file
    from reviewmymcp.ingest.normalizer import extract_server_meta
    from reviewmymcp.ingest.schema import Transport
    from reviewmymcp.scoring.grader import grade_results

    _register_evaluators()

    if config_path:
        audit_config = AuditConfig.from_file(config_path)
    else:
        audit_config = AuditConfig.default()

    audit_config.judge.provider = judge_provider
    if judge_model:
        audit_config.judge.model = judge_model

    if not log_file and not target:
        click.echo("Error: provide either a target server or --log-file", err=True)
        sys.exit(2)

    if log_file:
        tp = Transport(transport) if transport else Transport.STDIO
        events = load_file(log_file, transport=tp, redact=not no_redact)
    else:
        import asyncio

        events = asyncio.run(_run_synthetic_audit(target, transport, not no_redact, audit_config))

    events = correlate(events)
    server_meta = extract_server_meta(events)
    sessions = get_sessions(events)

    dim_list = [d.strip() for d in dimensions.split(",")] if dimensions else None

    eval_config = EvaluatorConfig(
        thresholds=audit_config.thresholds,
        judge_provider=audit_config.judge.provider,
        judge_model=audit_config.judge.model,
        extra=audit_config.model_dump(),
    )
    if no_llm_judges:
        eval_config.enabled_checks = []

    results = run_all(events, server_meta, eval_config, dimensions=dim_list)
    report = grade_results(results, server_meta, total_events=len(events), total_sessions=len(sessions))

    _output_report(report, output_format, output_file)
    sys.exit(1 if any(f.severity.value in ("critical", "high") for f in report.top_findings) else 0)


@cli.command()
@click.argument("log_file", type=click.Path(exists=True))
@click.option(
    "--output",
    "output_format",
    type=click.Choice(["terminal", "json", "html", "sarif"]),
    default="terminal",
)
@click.option("--output-file", type=click.Path(), default=None)
@click.option("--dimensions", type=str, default=None)
@click.option("--no-redact", is_flag=True, default=False)
@click.option("--no-llm-judges", is_flag=True, default=False)
def replay(
    log_file: str,
    output_format: str,
    output_file: str | None,
    dimensions: str | None,
    no_redact: bool,
    no_llm_judges: bool,
) -> None:
    """Replay a captured log file through evaluators."""
    from reviewmymcp.evaluators.base import EvaluatorConfig
    from reviewmymcp.evaluators.registry import run_all
    from reviewmymcp.ingest.correlator import correlate, get_sessions
    from reviewmymcp.ingest.file_loader import load_file
    from reviewmymcp.ingest.normalizer import extract_server_meta
    from reviewmymcp.scoring.grader import grade_results

    _register_evaluators()

    events = load_file(log_file, redact=not no_redact)
    events = correlate(events)
    server_meta = extract_server_meta(events)
    sessions = get_sessions(events)

    dim_list = [d.strip() for d in dimensions.split(",")] if dimensions else None
    eval_config = EvaluatorConfig()
    results = run_all(events, server_meta, eval_config, dimensions=dim_list)
    report = grade_results(results, server_meta, total_events=len(events), total_sessions=len(sessions))

    _output_report(report, output_format, output_file)


@cli.command()
@click.argument("baseline", type=click.Path(exists=True))
@click.argument("current", type=click.Path(exists=True))
def diff(baseline: str, current: str) -> None:
    """Compare two audit reports for regressions."""

    from reviewmymcp.scoring.differ import diff_reports
    from reviewmymcp.scoring.grader import AuditReport

    baseline_report = AuditReport.model_validate_json(Path(baseline).read_text())
    current_report = AuditReport.model_validate_json(Path(current).read_text())

    result = diff_reports(baseline_report, current_report)

    console = Console()
    console.print(f"\nBaseline: {result.baseline_grade.value} → Current: {result.current_grade.value}")

    for dd in result.dimension_diffs:
        indicator = ""
        if dd.is_regression:
            indicator = " [red]↓ REGRESSION[/red]"
        elif dd.is_improvement:
            indicator = " [green]↑ improved[/green]"
        console.print(f"  {dd.dimension}: {dd.baseline_grade.value} → {dd.current_grade.value}{indicator}")

    if result.new_findings:
        console.print(f"\n[red]New findings: {len(result.new_findings)}[/red]")
        for f in result.new_findings[:5]:
            console.print(f"  [{f.severity.value.upper()}] {f.check_id}: {f.title}")

    if result.resolved_findings:
        console.print(f"\n[green]Resolved: {len(result.resolved_findings)}[/green]")

    sys.exit(1 if result.has_regressions else 0)


@cli.command("list-checks")
def list_checks() -> None:
    """List all available evaluator checks."""
    _register_evaluators()
    from reviewmymcp.evaluators.registry import get_all

    console = Console()

    from rich.table import Table

    t = Table(title="Available Checks")
    t.add_column("Dimension")
    t.add_column("Checks")

    for evaluator in sorted(get_all(), key=lambda e: e.dimension):
        checks = []
        from reviewmymcp.evaluators.base import EvaluatorConfig
        from reviewmymcp.ingest.schema import ServerMeta

        result = evaluator.evaluate([], ServerMeta(), EvaluatorConfig())
        checks = result.checks_run
        t.add_row(evaluator.dimension, "\n".join(checks))

    console.print(t)


async def _run_synthetic_audit(
    target: str,
    transport: str | None,
    redact: bool,
    audit_config,
) -> list:
    """Run synthetic traffic against a live server and return captured events."""
    from reviewmymcp.ingest.schema import ToolDefinition
    from reviewmymcp.synthetic.agent_driver import StdioAgentDriver
    from reviewmymcp.synthetic.scenario_planner import ScenarioPlanner

    click.echo(f"Starting synthetic audit against: {target}", err=True)

    if transport == "http" or target.startswith("http"):
        click.echo("HTTP synthetic audit not yet supported. Use stdio (pass a server command).", err=True)
        sys.exit(2)

    server_command = target.split()
    driver = StdioAgentDriver(server_command=server_command, redact=redact)

    try:
        await driver.start()
        click.echo("  Initializing server...", err=True)
        await driver.initialize()
        await driver.send_initialized()

        click.echo("  Discovering tools...", err=True)
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

        click.echo(f"  Found {len(tools)} tools. Running scenarios...", err=True)

        judge_provider = None
        if audit_config.judge.provider == "anthropic":
            from reviewmymcp.judge.anthropic_judge import AnthropicJudge

            judge_provider = AnthropicJudge(model=audit_config.judge.model)
        elif audit_config.judge.provider == "gemini":
            from reviewmymcp.judge.gemini_judge import GeminiJudge

            judge_provider = GeminiJudge(model=audit_config.judge.model)
        elif audit_config.judge.provider == "openai":
            from reviewmymcp.judge.openai_judge import OpenAIJudge

            judge_provider = OpenAIJudge(model=audit_config.judge.model)

        if judge_provider:
            try:
                planner = ScenarioPlanner(judge_provider)
                scenarios = await planner.plan_scenarios(tools, count=min(10, max(5, len(tools))))
                click.echo(f"  Generated {len(scenarios)} scenarios. Executing...", err=True)
                for scenario in scenarios:
                    await driver.execute_scenario(scenario)
            except Exception:
                click.echo("  LLM scenario planning failed, using fallback scenarios.", err=True)
                planner = ScenarioPlanner(judge_provider)
                scenarios = planner._fallback_scenarios(tools, 5)
                for scenario in scenarios:
                    await driver.execute_scenario(scenario)
        else:
            planner_cls = ScenarioPlanner.__new__(ScenarioPlanner)
            scenarios = planner_cls._fallback_scenarios(tools, 5)
            for scenario in scenarios:
                await driver.execute_scenario(scenario)

        click.echo("  Running edge probes...", err=True)
        await driver.execute_edge_probes(tools)

        click.echo(f"  Captured {len(driver.events)} events.", err=True)
        return driver.events

    finally:
        await driver.stop()


@cli.command()
@click.argument("target")
@click.option("--output-dir", type=click.Path(), default=".", help="Directory for log files")
@click.option("--transport", type=click.Choice(["stdio", "http"]), default=None)
@click.option("--no-redact", is_flag=True, default=False)
def watch(target: str, output_dir: str, transport: str | None, no_redact: bool) -> None:
    """Start a transparent proxy and continuously capture traffic."""
    import asyncio

    if transport == "http" or target.startswith("http"):
        click.echo(f"Starting HTTP proxy for {target}...")
        from reviewmymcp.proxy.http_proxy import create_proxy_app

        app, proxy = create_proxy_app(upstream_url=target, redact=not no_redact)

        import uvicorn

        log_path = Path(output_dir) / "mcp_traffic.ndjson"
        click.echo(f"Logging to {log_path}")

        import json

        log_handle = open(log_path, "a", encoding="utf-8")

        def on_event(event):
            record = {
                "direction": event.direction.value,
                "timestamp": event.timestamp.isoformat(),
                "session_id": event.session_id,
                **(event.raw_message or {}),
            }
            log_handle.write(json.dumps(record) + "\n")
            log_handle.flush()

        proxy.on_event(on_event)
        click.echo("Proxy listening on http://127.0.0.1:8080")
        uvicorn.run(app, host="127.0.0.1", port=8080, log_level="warning")
        log_handle.close()
    else:
        click.echo(f"Starting stdio proxy for: {target}")
        from reviewmymcp.proxy.stdio_proxy import run_stdio_proxy

        log_path = Path(output_dir) / "mcp_traffic.ndjson"
        click.echo(f"Logging to {log_path}", err=True)
        server_command = target.split()
        exit_code, events = asyncio.run(run_stdio_proxy(server_command, log_file=str(log_path), redact=not no_redact))
        click.echo(f"Server exited with code {exit_code}. Captured {len(events)} events.", err=True)
        sys.exit(exit_code)


def _output_report(report, output_format: str, output_file: str | None) -> None:
    if output_format == "terminal":
        from reviewmymcp.reporting.terminal import render_report

        render_report(report)
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
