"""Scorer — converts active-check observations into Findings and an AuditReport.

Maps behavioral signals from agent sessions into the same Finding/EvaluatorResult
types used by the passive evaluators, so active and passive reports are comparable.
"""

from __future__ import annotations

from reviewmymcp.active.observer import SessionAnalysis, SignalType
from reviewmymcp.evaluators.base import EvaluatorResult, Finding, Severity
from reviewmymcp.ingest.schema import ServerMeta
from reviewmymcp.scoring.grader import AuditReport, grade_results


SIGNAL_TO_FINDING: dict[SignalType, tuple[str, Severity, str]] = {
    SignalType.TOOL_NOT_FOUND: (
        "active.tool-not-discovered",
        Severity.HIGH,
        "Improve tool naming and descriptions so agents can find the right tool for a task.",
    ),
    SignalType.WRONG_TOOL_CHOSEN: (
        "active.wrong-tool-selected",
        Severity.MEDIUM,
        "Clarify tool descriptions to reduce ambiguity between tools.",
    ),
    SignalType.ARG_STRUGGLE: (
        "active.argument-difficulty",
        Severity.MEDIUM,
        "Add examples, improve parameter descriptions, or simplify the input schema.",
    ),
    SignalType.ERROR_NOT_RECOVERED: (
        "active.unrecoverable-error",
        Severity.HIGH,
        "Improve error messages to include actionable guidance the agent can use to retry.",
    ),
    SignalType.UNHELPFUL_ERROR: (
        "active.unhelpful-error-message",
        Severity.MEDIUM,
        "Return specific, actionable error messages that explain what went wrong and how to fix it.",
    ),
    SignalType.GAVE_UP: (
        "active.agent-gave-up",
        Severity.HIGH,
        "The MCP server was too difficult for an agent to use. Review tool design holistically.",
    ),
    SignalType.TASK_FAILED: (
        "active.task-timeout",
        Severity.MEDIUM,
        "Agent could not complete the task within the turn limit. Simplify workflows or improve guidance.",
    ),
    SignalType.EXCESSIVE_TURNS: (
        "active.inefficient-workflow",
        Severity.LOW,
        "Agent needed many turns to complete a simple task. Consider improving discoverability.",
    ),
    SignalType.CHAINING_FAILURE: (
        "active.chaining-failure",
        Severity.MEDIUM,
        "Tool outputs should be structured to feed naturally into other tool inputs.",
    ),
    SignalType.TOOL_CONFUSION: (
        "active.tool-confusion",
        Severity.MEDIUM,
        "Agent was confused about which tool to use. Reduce naming/description overlap.",
    ),
    SignalType.DESCRIPTION_MISMATCH: (
        "active.description-mismatch",
        Severity.HIGH,
        "Tool behavior did not match its description. Update the description to reflect actual behavior.",
    ),
}

POSITIVE_SIGNALS = {
    SignalType.TOOL_FOUND,
    SignalType.CORRECT_ARGS_FIRST_TRY,
    SignalType.ERROR_RECOVERED,
    SignalType.TASK_COMPLETED,
    SignalType.CHAINING_SUCCESS,
}

DIMENSION_MAP: dict[str, str] = {
    "active.tool-not-discovered": "discoverability",
    "active.wrong-tool-selected": "discoverability",
    "active.argument-difficulty": "discoverability",
    "active.unrecoverable-error": "reliability",
    "active.unhelpful-error-message": "reliability",
    "active.agent-gave-up": "composability",
    "active.task-timeout": "composability",
    "active.inefficient-workflow": "efficiency",
    "active.chaining-failure": "composability",
    "active.tool-confusion": "discoverability",
    "active.description-mismatch": "accuracy",
}


def score_sessions(
    analyses: list[SessionAnalysis],
    server_meta: ServerMeta,
) -> AuditReport:
    """Convert session analyses into an AuditReport using the standard grading engine."""
    dimension_findings: dict[str, list[Finding]] = {}
    all_check_ids: set[str] = set()

    for analysis in analyses:
        for obs in analysis.observations:
            if obs.signal in POSITIVE_SIGNALS:
                continue

            mapping = SIGNAL_TO_FINDING.get(obs.signal)
            if not mapping:
                continue

            check_id, severity, remediation = mapping
            all_check_ids.add(check_id)
            dimension = DIMENSION_MAP.get(check_id, "composability")

            finding = Finding(
                check_id=check_id,
                severity=severity,
                title=obs.details,
                description=f"[{obs.category.value}] task={obs.task_id}",
                evidence=obs.evidence,
                remediation=remediation,
                affected_entity=", ".join(obs.affected_tools) if obs.affected_tools else obs.task_id,
            )

            dimension_findings.setdefault(dimension, []).append(finding)

    results: list[EvaluatorResult] = []
    for dim, findings in dimension_findings.items():
        checks = sorted({f.check_id for f in findings})
        results.append(EvaluatorResult(
            dimension=dim,
            checks_run=checks,
            findings=findings,
            stats=_compute_stats(analyses, dim),
        ))

    _ensure_all_dimensions(results, analyses)

    total_sessions = len(analyses)
    return grade_results(results, server_meta, total_events=0, total_sessions=total_sessions)


def _compute_stats(analyses: list[SessionAnalysis], dimension: str) -> dict:
    total_tasks = len(analyses)
    completed = sum(1 for a in analyses if a.success)
    return {
        "total_tasks": total_tasks,
        "completed_tasks": completed,
        "completion_rate": completed / total_tasks if total_tasks else 0,
    }


def _ensure_all_dimensions(results: list[EvaluatorResult], analyses: list[SessionAnalysis]) -> None:
    """Add empty results for dimensions that had no findings (so they show as grade A)."""
    present = {r.dimension for r in results}
    for dim in ["discoverability", "reliability", "composability", "efficiency", "accuracy"]:
        if dim not in present:
            results.append(EvaluatorResult(
                dimension=dim,
                checks_run=[],
                findings=[],
                stats=_compute_stats(analyses, dim),
            ))


def compute_summary_stats(analyses: list[SessionAnalysis]) -> dict:
    """Compute aggregate stats across all sessions for reporting."""
    total = len(analyses)
    completed = sum(1 for a in analyses if a.success)
    gave_up = sum(1 for a in analyses if not a.success)

    all_obs = [obs for a in analyses for obs in a.observations]
    positive = sum(1 for o in all_obs if o.signal in POSITIVE_SIGNALS)
    negative = sum(1 for o in all_obs if o.signal in SIGNAL_TO_FINDING)

    tools_used = set()
    for a in analyses:
        tools_used.update(a.tools_called)

    avg_turns = sum(a.turns_used for a in analyses) / total if total else 0

    by_category: dict[str, dict[str, int]] = {}
    for a in analyses:
        cat = a.category.value
        by_category.setdefault(cat, {"total": 0, "success": 0})
        by_category[cat]["total"] += 1
        if a.success:
            by_category[cat]["success"] += 1

    return {
        "total_tasks": total,
        "completed": completed,
        "failed": gave_up,
        "completion_rate": completed / total if total else 0,
        "positive_signals": positive,
        "negative_signals": negative,
        "unique_tools_used": sorted(tools_used),
        "avg_turns_per_task": round(avg_turns, 1),
        "by_category": by_category,
    }
