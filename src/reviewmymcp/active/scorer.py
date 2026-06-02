"""Scorer for Prod2 active audit — scores across 5 usability dimensions."""

from __future__ import annotations

from collections import Counter

from reviewmymcp.active.models import (
    ActiveAuditReport,
    ActiveDimensionScore,
    BehavioralSignal,
    TaskExecution,
)
from reviewmymcp.ingest.schema import ServerMeta

DIMENSIONS = [
    "tool_discovery",
    "argument_quality",
    "task_completion",
    "error_recovery",
    "multi_step_reasoning",
    "security",
]

GRADE_THRESHOLDS = [(90, "A"), (75, "B"), (60, "C"), (40, "D"), (0, "F")]


def _grade_from_score(score: float) -> str:
    for threshold, grade in GRADE_THRESHOLDS:
        if score >= threshold:
            return grade
    return "F"


def score_executions(
    executions: list[TaskExecution],
    server_meta: ServerMeta,
) -> ActiveAuditReport:
    """Score task executions across 5 dimensions and produce the active audit report."""
    all_signals: list[BehavioralSignal] = []
    for ex in executions:
        all_signals.extend(ex.signals)

    signal_counts = Counter(all_signals)
    outcome_counts = Counter(ex.outcome for ex in executions)
    total_tasks = len(executions)

    dimension_scores = [
        _score_tool_discovery(signal_counts, total_tasks),
        _score_argument_quality(signal_counts, executions),
        _score_task_completion(outcome_counts, total_tasks),
        _score_error_recovery(signal_counts),
        _score_multi_step(signal_counts),
        _score_security(signal_counts, executions),
    ]

    return ActiveAuditReport(
        server_meta=server_meta,
        task_executions=executions,
        dimension_scores=dimension_scores,
        total_tasks=total_tasks,
        total_turns=sum(ex.total_turns for ex in executions),
    )


def _score_tool_discovery(counts: Counter, total_tasks: int) -> ActiveDimensionScore:
    found = counts.get(BehavioralSignal.TOOL_FOUND, 0)
    not_found = counts.get(BehavioralSignal.TOOL_NOT_FOUND, 0)
    total = found + not_found

    score = (found / total * 100) if total > 0 else 100.0

    return ActiveDimensionScore(
        dimension="tool_discovery",
        score=round(score, 1),
        grade=_grade_from_score(score),
        signal_counts={
            "tool_found": found,
            "tool_not_found": not_found,
            "correct_tool": counts.get(BehavioralSignal.CORRECT_TOOL_SELECTED, 0),
            "wrong_tool": counts.get(BehavioralSignal.WRONG_TOOL_SELECTED, 0),
        },
    )


def _score_argument_quality(counts: Counter, executions: list[TaskExecution]) -> ActiveDimensionScore:
    struggles = counts.get(BehavioralSignal.ARGUMENT_STRUGGLE, 0)
    # Count total tool call attempts across all executions
    total_calls = sum(len(tc) for ex in executions for turn in ex.turns for tc in [turn.tool_calls])

    if total_calls == 0:
        score = 100.0
    else:
        # Each struggle is a 15-point deduction per call
        deduction = (struggles / total_calls) * 60
        score = max(0, 100.0 - deduction)

    return ActiveDimensionScore(
        dimension="argument_quality",
        score=round(score, 1),
        grade=_grade_from_score(score),
        signal_counts={
            "argument_struggle": struggles,
            "total_tool_calls": total_calls,
        },
    )


def _score_task_completion(outcome_counts: Counter, total_tasks: int) -> ActiveDimensionScore:
    if total_tasks == 0:
        return ActiveDimensionScore(dimension="task_completion", score=100.0, grade="A")

    successes = outcome_counts.get("success", 0)
    partials = outcome_counts.get("partial", 0)

    # Success = full credit, partial = half credit
    effective = successes + partials * 0.5
    score = (effective / total_tasks) * 100

    return ActiveDimensionScore(
        dimension="task_completion",
        score=round(score, 1),
        grade=_grade_from_score(score),
        task_outcomes=dict(outcome_counts),
    )


def _score_error_recovery(counts: Counter) -> ActiveDimensionScore:
    recovered = counts.get(BehavioralSignal.ERROR_RECOVERED, 0)
    not_recovered = counts.get(BehavioralSignal.ERROR_NOT_RECOVERED, 0)
    total = recovered + not_recovered

    score = (recovered / total * 100) if total > 0 else 100.0

    return ActiveDimensionScore(
        dimension="error_recovery",
        score=round(score, 1),
        grade=_grade_from_score(score),
        signal_counts={
            "error_recovered": recovered,
            "error_not_recovered": not_recovered,
        },
    )


def _score_multi_step(counts: Counter) -> ActiveDimensionScore:
    success = counts.get(BehavioralSignal.CHAINING_SUCCESS, 0)
    failure = counts.get(BehavioralSignal.CHAINING_FAILURE, 0)
    total = success + failure

    score = (success / total * 100) if total > 0 else 100.0

    return ActiveDimensionScore(
        dimension="multi_step_reasoning",
        score=round(score, 1),
        grade=_grade_from_score(score),
        signal_counts={
            "chaining_success": success,
            "chaining_failure": failure,
        },
    )


def _score_security(counts: Counter, executions: list[TaskExecution]) -> ActiveDimensionScore:
    injection_desc = counts.get(BehavioralSignal.INJECTION_IN_DESCRIPTION, 0)
    injection_output = counts.get(BehavioralSignal.INJECTION_IN_OUTPUT, 0)
    untrusted = counts.get(BehavioralSignal.UNTRUSTED_CONTENT_NO_PROVENANCE, 0)
    total_calls = sum(len(turn.tool_calls) for execution in executions for turn in execution.turns)
    exposure_count = injection_desc + injection_output + untrusted

    if total_calls == 0 and exposure_count == 0:
        score = 100.0
    else:
        deduction = injection_output * 40 + injection_desc * 30 + untrusted * 15
        score = max(0.0, 100.0 - deduction)

    return ActiveDimensionScore(
        dimension="security",
        score=round(score, 1),
        grade=_grade_from_score(score),
        signal_counts={
            "injection_in_description": injection_desc,
            "injection_in_output": injection_output,
            "untrusted_content_no_provenance": untrusted,
            "total_tool_calls": total_calls,
        },
    )
