"""CLI entry points for reviewmymcp."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import urllib.request
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
    from reviewmymcp.evaluators.provenance.checks import ProvenanceEvaluator
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
        ProvenanceEvaluator,
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


def _build_judge(provider_name: str, model: str):
    """Construct a JudgeProvider from config strings. Returns None on failure."""
    if provider_name == "anthropic":
        from reviewmymcp.judge.anthropic_judge import AnthropicJudge
        return AnthropicJudge(model=model) if model else AnthropicJudge()
    elif provider_name == "gemini":
        from reviewmymcp.judge.gemini_judge import GeminiJudge
        return GeminiJudge(model=model) if model else GeminiJudge()
    elif provider_name == "openai":
        from reviewmymcp.judge.openai_judge import OpenAIJudge
        return OpenAIJudge(model=model) if model else OpenAIJudge()
    return None


def _merge_auth_scopes(discovered: list[str], configured: list[str] | tuple[str, ...]) -> list[str]:
    return sorted({scope for scope in [*discovered, *configured] if scope})


PERSISTENCE_ENV_SUFFIXES = (
    "_FILE_PATH",
    "_STORAGE_PATH",
    "_DATA_PATH",
    "_DB_PATH",
    "_DATABASE_PATH",
)


def _parse_keyed_paths(values: tuple[str, ...]) -> dict[str, str]:
    paths: dict[str, str] = {}
    for value in values:
        key, sep, path = value.partition("=")
        if not sep or not key.strip() or not path.strip():
            raise click.BadParameter("expected KEY=PATH", param_hint="--persistence-path")
        paths[key.strip()] = path.strip()
    return paths


def _capture_env_persistence_paths() -> dict[str, str]:
    paths: dict[str, str] = {}
    for key, value in sorted(os.environ.items()):
        if value and key.endswith(PERSISTENCE_ENV_SUFFIXES):
            paths[f"env:{key}"] = value
    return paths


def _resolve_runtime_metadata(command: list[str]):
    from reviewmymcp.ingest.schema import RuntimeMetadata

    meta = RuntimeMetadata(command=command, reproducible_command=shlex.join(command))
    if not command:
        return meta

    executable = shutil.which(command[0])
    if executable:
        meta.executable_sha256 = _sha256_file(executable)

    package_spec = _npm_package_spec(command)
    if package_spec:
        package_name, requested_version = _split_npm_package_spec(package_spec)
        if requested_version and _is_exact_npm_version(requested_version):
            resolved_version = requested_version
        else:
            resolved_version = _npm_view_version(package_spec if requested_version else package_name)
        meta.package_manager = "npm"
        meta.package_name = package_name
        meta.package_version = resolved_version
        if resolved_version:
            pinned_spec = f"{package_name}@{resolved_version}"
            meta.reproducible_command = shlex.join(_replace_package_spec(command, package_spec, pinned_spec))
    return meta


def _resolve_npm_provenance(package_name: str):
    from reviewmymcp.ingest.schema import ProvenanceMetadata

    data = _npm_view_package_json(package_name)
    provenance = ProvenanceMetadata()
    if not isinstance(data, dict):
        return provenance

    repository = data.get("repository")
    if isinstance(repository, dict):
        provenance.repository_url = _normalize_repository_url(repository.get("url", ""))
    elif isinstance(repository, str):
        provenance.repository_url = _normalize_repository_url(repository)
    if provenance.repository_url:
        provenance.source_reachable = _url_reachable(provenance.repository_url)

    license_declared = data.get("license")
    if isinstance(license_declared, str):
        provenance.license_declared = license_declared

    publish_times = data.get("time")
    if isinstance(publish_times, dict):
        provenance.version_publish_times = [
            value
            for key, value in publish_times.items()
            if key not in {"created", "modified"} and isinstance(value, str)
        ]
    return provenance


def _npm_package_spec(command: list[str]) -> str:
    if not command:
        return ""
    verb = Path(command[0]).name
    args = command[1:]
    if verb == "npm" and args[:1] == ["exec"]:
        args = args[1:]
    elif verb not in {"npx", "npm", "pnpm", "yarn", "bunx"}:
        return ""

    skip_next = False
    for index, arg in enumerate(args):
        if skip_next:
            skip_next = False
            continue
        if arg in {"--package", "-p"} and index + 1 < len(args):
            return args[index + 1]
        if arg in {"--yes", "-y", "--quiet"}:
            continue
        if arg.startswith("-"):
            if "=" not in arg:
                skip_next = arg in {"--cache", "--userconfig", "--registry"}
            continue
        return arg
    return ""


def _split_npm_package_spec(spec: str) -> tuple[str, str]:
    version_sep = spec.rfind("@")
    if version_sep > 0:
        return spec[:version_sep], spec[version_sep + 1:]
    return spec, ""


def _is_exact_npm_version(version: str) -> bool:
    return bool(re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", version))


def _replace_package_spec(command: list[str], old: str, new: str) -> list[str]:
    return [new if part == old else part for part in command]


def _npm_view_version(package_name: str) -> str:
    try:
        result = subprocess.run(
            ["npm", "view", package_name, "version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""


def _npm_view_package_json(package_name: str) -> dict:
    try:
        result = subprocess.run(
            ["npm", "view", package_name, "--json"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if result.returncode != 0:
        return {}
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _normalize_repository_url(url: str) -> str:
    normalized = url.strip()
    if normalized.startswith("git+"):
        normalized = normalized[4:]
    if normalized.startswith("git://github.com/"):
        normalized = normalized.replace("git://github.com/", "https://github.com/", 1)
    if normalized.endswith(".git"):
        normalized = normalized[:-4]
    return normalized


def _url_reachable(url: str) -> bool | None:
    try:
        request = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status < 400
    except Exception:
        return False


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return ""
    return digest.hexdigest()


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
@click.option("--auth-scope", multiple=True, help="Auth scope present in the audit token; repeat for multiple scopes")
@click.option("--persistence-path", multiple=True, help="Known persistence location as KEY=PATH; repeat for multiple paths")
@click.option("--stress", is_flag=True, default=False, help="Run high-volume stress traffic against live stdio targets")
@click.option("--stress-calls", type=click.IntRange(min=1), default=50, show_default=True)
@click.option("--stress-duration", type=float, default=30.0, show_default=True)
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
    auth_scope: tuple[str, ...],
    persistence_path: tuple[str, ...],
    stress: bool,
    stress_calls: int,
    stress_duration: float,
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
    cli_persistence_paths = _parse_keyed_paths(persistence_path)

    if not log_file and not target:
        click.echo("Error: provide either a target server or --log-file", err=True)
        sys.exit(2)

    if log_file:
        if stress:
            click.echo("Stress replay mode: evaluating any stress-tagged events in the log; no traffic will be issued.", err=True)
        tp = Transport(transport) if transport else Transport.STDIO
        events = load_file(log_file, transport=tp, redact=not no_redact)
    else:
        import asyncio

        events = asyncio.run(
            _run_synthetic_audit(
                target,
                transport,
                not no_redact,
                audit_config,
                stress=stress,
                stress_calls=stress_calls,
                stress_duration=stress_duration,
            )
        )

    events = correlate(events)
    server_meta = extract_server_meta(events)
    if not log_file and target:
        server_meta.runtime = _resolve_runtime_metadata(target.split())
        if server_meta.runtime.package_manager == "npm" and server_meta.runtime.package_name:
            server_meta.provenance = _resolve_npm_provenance(server_meta.runtime.package_name)
    server_meta.auth.scopes_used = _merge_auth_scopes(
        server_meta.auth.scopes_used,
        [*audit_config.auth.scopes_used, *auth_scope],
    )
    server_meta.persistence.paths = {
        **_capture_env_persistence_paths(),
        **audit_config.persistence.paths,
        **cli_persistence_paths,
    }
    server_meta.persistence.working_directory = audit_config.persistence.working_directory
    server_meta.persistence.package_directory = audit_config.persistence.package_directory
    if not log_file and not server_meta.persistence.working_directory:
        server_meta.persistence.working_directory = str(Path.cwd())
    sessions = get_sessions(events)

    dim_list = [d.strip() for d in dimensions.split(",")] if dimensions else None

    judge_adapter = None
    if not no_llm_judges:
        from reviewmymcp.judge.base import SyncJudgeAdapter
        judge_instance = _build_judge(audit_config.judge.provider, audit_config.judge.model)
        if judge_instance:
            judge_adapter = SyncJudgeAdapter(judge_instance)

    eval_config = EvaluatorConfig(
        thresholds=audit_config.thresholds,
        judge_provider=audit_config.judge.provider,
        judge_model=audit_config.judge.model,
        judge=judge_adapter,
        extra=audit_config.model_dump(),
    )

    results = run_all(events, server_meta, eval_config, dimensions=dim_list)

    tool_count = len(server_meta.tools)
    call_count = sum(1 for e in events if e.is_request and e.method == "tools/call")
    report = grade_results(
        results, server_meta,
        total_events=len(events), total_sessions=len(sessions),
        tool_count=tool_count, call_count=call_count,
    )

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
@click.option("--judge-provider", type=click.Choice(["anthropic", "gemini", "openai"]), default="anthropic")
@click.option("--judge-model", type=str, default="")
@click.option("--auth-scope", multiple=True, help="Auth scope present in the audit token; repeat for multiple scopes")
@click.option("--persistence-path", multiple=True, help="Known persistence location as KEY=PATH; repeat for multiple paths")
@click.option("--stress", is_flag=True, default=False, help="Evaluate stress-tagged events in the replay log")
@click.option("--stress-calls", type=click.IntRange(min=1), default=50, show_default=True)
@click.option("--stress-duration", type=float, default=30.0, show_default=True)
def replay(
    log_file: str,
    output_format: str,
    output_file: str | None,
    dimensions: str | None,
    no_redact: bool,
    no_llm_judges: bool,
    judge_provider: str,
    judge_model: str,
    auth_scope: tuple[str, ...],
    persistence_path: tuple[str, ...],
    stress: bool,
    stress_calls: int,
    stress_duration: float,
) -> None:
    """Replay a captured log file through evaluators."""
    from reviewmymcp.evaluators.base import EvaluatorConfig
    from reviewmymcp.evaluators.registry import run_all
    from reviewmymcp.ingest.correlator import correlate, get_sessions
    from reviewmymcp.ingest.file_loader import load_file
    from reviewmymcp.ingest.normalizer import extract_server_meta
    from reviewmymcp.scoring.grader import grade_results

    _register_evaluators()
    cli_persistence_paths = _parse_keyed_paths(persistence_path)
    if stress:
        click.echo("Stress replay mode: evaluating stress-tagged events already present in the log.", err=True)
    _ = (stress_calls, stress_duration)

    events = load_file(log_file, redact=not no_redact)
    events = correlate(events)
    server_meta = extract_server_meta(events)
    server_meta.auth.scopes_used = _merge_auth_scopes(server_meta.auth.scopes_used, auth_scope)
    server_meta.persistence.paths = cli_persistence_paths
    sessions = get_sessions(events)

    judge_adapter = None
    if not no_llm_judges:
        from reviewmymcp.judge.base import SyncJudgeAdapter
        judge_instance = _build_judge(judge_provider, judge_model)
        if judge_instance:
            judge_adapter = SyncJudgeAdapter(judge_instance)

    dim_list = [d.strip() for d in dimensions.split(",")] if dimensions else None
    eval_config = EvaluatorConfig(
        judge_provider=judge_provider,
        judge_model=judge_model,
        judge=judge_adapter,
    )
    results = run_all(events, server_meta, eval_config, dimensions=dim_list)

    tool_count = len(server_meta.tools)
    call_count = sum(1 for e in events if e.is_request and e.method == "tools/call")
    report = grade_results(
        results, server_meta,
        total_events=len(events), total_sessions=len(sessions),
        tool_count=tool_count, call_count=call_count,
    )

    _output_report(report, output_format, output_file)
    sys.exit(1 if any(f.severity.value in ("critical", "high") for f in report.top_findings) else 0)


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

    for dd in result.dimension_diffs:
        indicator = ""
        if dd.is_regression:
            indicator = " [red]↓ REGRESSION[/red]"
        elif dd.is_improvement:
            indicator = " [green]↑ improved[/green]"
        console.print(
            f"  {dd.dimension}: {dd.baseline_grade.value} ({dd.baseline_score:.0f})"
            f" → {dd.current_grade.value} ({dd.current_score:.0f}){indicator}"
        )

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


@cli.command("active-audit")
@click.argument("target")
@click.option("--agent-provider", type=click.Choice(["anthropic", "gemini", "openai"]), default="anthropic")
@click.option("--agent-model", type=str, default="")
@click.option("--judge-provider", type=click.Choice(["anthropic", "gemini", "openai"]), default="anthropic")
@click.option("--judge-model", type=str, default="")
@click.option("--max-turns", type=int, default=15)
@click.option(
    "--output",
    "output_format",
    type=click.Choice(["terminal", "json"]),
    default="terminal",
)
@click.option("--output-file", type=click.Path(), default=None)
@click.option("--categories", type=str, default=None, help="Comma-separated task categories")
@click.option(
    "--readonly",
    is_flag=True,
    default=False,
    help="Filter mutating tools out of the agent's toolset. Recommended for credentialed live servers.",
)
@click.option(
    "--allow-mutations",
    is_flag=True,
    default=False,
    help="Allow the agent to call mutating tools. Use only against throwaway/test accounts.",
)
@click.option(
    "--trace-file",
    type=click.Path(),
    default=None,
    help="Write per-turn JSONL trace of agent actions to this path.",
)
def active_audit(
    target: str,
    agent_provider: str,
    agent_model: str,
    judge_provider: str,
    judge_model: str,
    max_turns: int,
    output_format: str,
    output_file: str | None,
    categories: str | None,
    readonly: bool,
    allow_mutations: bool,
    trace_file: str | None,
) -> None:
    """Run active agent-driven usability testing against a live MCP server."""
    import asyncio

    asyncio.run(
        _run_active_audit(
            target, agent_provider, agent_model, judge_provider, judge_model,
            max_turns, output_format, output_file, categories,
            readonly, allow_mutations, trace_file,
        )
    )


async def _run_active_audit(
    target: str,
    agent_provider_name: str,
    agent_model: str,
    judge_provider_name: str,
    judge_model: str,
    max_turns: int,
    output_format: str,
    output_file: str | None,
    categories_str: str | None,
    readonly: bool,
    allow_mutations: bool,
    trace_file: str | None,
) -> None:
    """Async implementation of active-audit."""
    from reviewmymcp.active.agent_loop import AgentLoop
    from reviewmymcp.active.models import TaskCategory
    from reviewmymcp.active.safety import classify_mutators, filter_readonly, write_trace
    from reviewmymcp.active.scorer import score_executions
    from reviewmymcp.active.task_generator import TaskGenerator
    from reviewmymcp.ingest.normalizer import extract_server_meta
    from reviewmymcp.ingest.schema import ToolDefinition
    from reviewmymcp.synthetic.agent_driver import StdioAgentDriver

    console = Console()

    if readonly and allow_mutations:
        click.echo("Error: --readonly and --allow-mutations are mutually exclusive.", err=True)
        sys.exit(2)

    # Parse categories
    if categories_str:
        cat_list = [TaskCategory(c.strip()) for c in categories_str.split(",")]
    else:
        cat_list = None

    # Build agent provider
    agent_prov = _build_agent_provider(agent_provider_name, agent_model)
    if agent_prov is None:
        click.echo(f"Error: could not build agent provider '{agent_provider_name}'", err=True)
        sys.exit(2)

    # Build judge for task generation
    judge_instance = _build_judge(judge_provider_name, judge_model)

    # Start MCP server
    server_command = target.split()
    driver = StdioAgentDriver(server_command=server_command, redact=True)

    try:
        await driver.start()
        console.print("[dim]Initializing server...[/dim]", highlight=False)
        await driver.initialize()
        await driver.send_initialized()

        console.print("[dim]Discovering tools...[/dim]", highlight=False)
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

        console.print(f"[dim]Found {len(tools)} tools.[/dim]", highlight=False)

        mutators = classify_mutators(tools)
        if mutators:
            if not readonly and not allow_mutations:
                lines = "\n".join(f"  - {n}" for n in mutators)
                click.echo(
                    f"Error: {len(mutators)} tool(s) match write-verb prefixes "
                    f"(potentially mutating):\n{lines}\n\n"
                    "Re-run with --readonly (filter them out of the agent's toolset) "
                    "or --allow-mutations (proceed; target MUST be a throwaway/test "
                    "account — credentialed live runs can create, delete, or "
                    "overwrite real assets).",
                    err=True,
                )
                sys.exit(2)
            if readonly:
                tools = filter_readonly(tools)
                console.print(
                    f"[dim]--readonly: dropped {len(mutators)} mutating tool(s), {len(tools)} remain.[/dim]",
                    highlight=False,
                )
            else:
                console.print(
                    f"[yellow]WARNING: --allow-mutations enabled with {len(mutators)} "
                    "mutating tool(s). Target must be a throwaway/test account.[/yellow]",
                    highlight=False,
                )

        console.print("[dim]Generating tasks...[/dim]", highlight=False)

        # Generate tasks
        generator = TaskGenerator(judge_provider=judge_instance)
        tasks = await generator.generate_tasks(tools, categories=cat_list)
        console.print(f"[dim]Generated {len(tasks)} tasks. Running agent...[/dim]", highlight=False)

        # Run agent loop for each task
        loop = AgentLoop(
            agent_provider=agent_prov,
            call_tool_fn=driver.call_tool,
            tools=tools,
            max_turns=max_turns,
        )

        executions = []
        for i, task in enumerate(tasks, 1):
            console.print(f"[dim]  Task {i}/{len(tasks)}: {task.description[:60]}...[/dim]", highlight=False)
            execution = await loop.execute_task(task)
            executions.append(execution)

        # Score
        server_meta = extract_server_meta(driver.events)
        report = score_executions(executions, server_meta)

        console.print(f"[dim]Completed {len(executions)} tasks in {report.total_turns} turns.[/dim]", highlight=False)

        if trace_file:
            write_trace(executions, Path(trace_file))
            console.print(f"[dim]Trace written to {trace_file}[/dim]", highlight=False)

        # Output
        _output_active_report(report, output_format, output_file, console)

    finally:
        await driver.stop()


def _build_agent_provider(provider_name: str, model: str):
    """Construct an AgentProvider from config strings."""
    if provider_name == "anthropic":
        from reviewmymcp.judge.anthropic_agent import AnthropicAgentProvider
        return AnthropicAgentProvider(model=model) if model else AnthropicAgentProvider()
    elif provider_name == "openai":
        from reviewmymcp.judge.openai_agent import OpenAIAgentProvider
        return OpenAIAgentProvider(model=model) if model else OpenAIAgentProvider()
    elif provider_name == "gemini":
        from reviewmymcp.judge.gemini_agent import GeminiAgentProvider
        return GeminiAgentProvider(model=model) if model else GeminiAgentProvider()
    return None


def _output_active_report(report, output_format: str, output_file: str | None, console) -> None:
    """Render the active audit report."""
    if output_format == "json":
        output = report.model_dump_json(indent=2)
        if output_file:
            Path(output_file).write_text(output, encoding="utf-8")
        else:
            click.echo(output)
    else:
        from rich.panel import Panel
        from rich.table import Table

        table = Table(title="Active Audit — Dimension Scores")
        table.add_column("Dimension")
        table.add_column("Score", justify="right")
        table.add_column("Grade", justify="center")

        for ds in report.dimension_scores:
            table.add_row(ds.dimension, f"{ds.score:.0f}", ds.grade)

        console.print(Panel(table))

        # Task outcomes
        console.print(f"\nTasks: {report.total_tasks}  Turns: {report.total_turns}")
        for ex in report.task_executions:
            status = {"success": "[green]OK[/green]", "partial": "[yellow]PARTIAL[/yellow]",
                       "failure": "[red]FAIL[/red]", "gave_up": "[red]GAVE UP[/red]"}.get(ex.outcome, ex.outcome)
            console.print(f"  [{ex.task.category.value}] {status} {ex.task.description[:70]}")

        if output_file:
            Path(output_file).write_text(report.model_dump_json(indent=2), encoding="utf-8")


@cli.command()
@click.argument("input_file", type=click.Path(exists=True))
@click.option(
    "--format",
    "log_format",
    type=click.Choice(["auto", "passthrough", "claude-desktop", "python-sdk"]),
    default="auto",
    help="Source log format (auto-detect by default)",
)
@click.option("--output-file", type=click.Path(), default=None, help="Output path (default: stdout)")
def convert(input_file: str, log_format: str, output_file: str | None) -> None:
    """Convert MCP logs from various sources to canonical NDJSON format."""
    import json

    from reviewmymcp.ingest.converter import build_registry

    registry = build_registry()
    input_path = Path(input_file)

    if log_format == "auto":
        # Auto-detect: try passthrough first (most common case)
        converter = registry.get("passthrough")
    else:
        converter = registry.get(log_format)

    if converter is None:
        click.echo(f"Error: unknown format '{log_format}'", err=True)
        sys.exit(2)

    try:
        records = converter.convert(input_path)
    except NotImplementedError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(2)
    except (ValueError, json.JSONDecodeError) as e:
        click.echo(f"Error parsing {input_file}: {e}", err=True)
        sys.exit(1)

    output = "\n".join(json.dumps(r) for r in records) + "\n"

    if output_file:
        Path(output_file).write_text(output, encoding="utf-8")
        click.echo(f"Wrote {len(records)} records to {output_file}", err=True)
    else:
        click.echo(output, nl=False)


async def _run_synthetic_audit(
    target: str,
    transport: str | None,
    redact: bool,
    audit_config,
    stress: bool = False,
    stress_calls: int = 50,
    stress_duration: float = 30.0,
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

        if stress:
            click.echo(
                f"  Stress mode enabled: {stress_calls} calls/tool over {stress_duration:.1f}s. "
                "This may trigger upstream rate limits.",
                err=True,
            )
            await driver.execute_stress(tools, calls_per_tool=stress_calls, duration_seconds=stress_duration)

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
