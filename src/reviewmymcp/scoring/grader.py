"""Grading engine: rolls up findings into per-dimension numeric scores and letter grades."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from reviewmymcp.evaluators.base import EvaluatorResult, Finding, Severity, SkippedCheck
from reviewmymcp.ingest.schema import ServerMeta


class Grade(StrEnum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    F = "F"


GRADE_ORDER = [Grade.A, Grade.B, Grade.C, Grade.D, Grade.F]

# Severity deduction weights — points deducted per finding before normalization.
SEVERITY_WEIGHT: dict[Severity, float] = {
    Severity.CRITICAL: 25.0,
    Severity.HIGH: 10.0,
    Severity.MEDIUM: 4.0,
    Severity.LOW: 1.0,
    Severity.INFO: 0.0,
}

# How each dimension normalizes its deductions.
# "tools" -> divide by tool_count, "calls" -> divide by call_count, None -> raw.
DIMENSION_DENOMINATOR: dict[str, str | None] = {
    "discoverability": "tools",
    "efficiency": "tools",
    "accuracy": "tools",
    "security": "tools",
    "reliability": "calls",
    "composability": "calls",
    "performance": "calls",
    "conformance": None,
    "compliance": None,
}


class DimensionScore(BaseModel):
    dimension: str
    score: float  # 0-100 numeric score
    grade: Grade
    critical_count: int = 0
    high_count: int = 0
    medium_count: int = 0
    low_count: int = 0
    info_count: int = 0
    findings: list[Finding] = Field(default_factory=list)
    checks_skipped: list[SkippedCheck] = Field(default_factory=list)
    denominator_type: str | None = None
    denominator_value: int = 0


class AuditReport(BaseModel):
    server_meta: ServerMeta
    dimension_scores: list[DimensionScore] = Field(default_factory=list)
    top_findings: list[Finding] = Field(default_factory=list)
    total_events: int = 0
    total_sessions: int = 0
    tool_count: int = 0
    call_count: int = 0
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


def _count_by_severity(findings: list[Finding]) -> dict[Severity, int]:
    counts: dict[Severity, int] = {s: 0 for s in Severity}
    for f in findings:
        counts[f.severity] += 1
    return counts


def _raw_deduction(findings: list[Finding]) -> float:
    """Sum severity-weighted deductions for a set of findings."""
    return sum(SEVERITY_WEIGHT[f.severity] for f in findings)


def _score_dimension(
    findings: list[Finding],
    denominator_type: str | None,
    tool_count: int,
    call_count: int,
) -> float:
    """Compute a 0-100 numeric score for a dimension.

    For normalized dimensions (tools/calls), deductions are per-unit:
      score = max(0, 100 - (raw_deduction / denominator) * 100 / scale_factor)
    The scale_factor controls how harsh the scoring is. With scale_factor=25,
    a raw deduction equal to the denominator produces a score of 0.

    For raw dimensions (conformance, compliance), deductions are absolute:
      score = max(0, 100 - raw_deduction)
    """
    raw = _raw_deduction(findings)
    if raw == 0:
        return 100.0

    if denominator_type == "tools" and tool_count > 0:
        per_unit = raw / tool_count
        # Scale: per_unit of 25 (one critical per tool) → score 0
        score = max(0.0, 100.0 - per_unit * 4.0)
    elif denominator_type == "calls" and call_count > 0:
        per_unit = raw / call_count
        score = max(0.0, 100.0 - per_unit * 4.0)
    else:
        # Raw: no normalization. 100 points of deductions → 0 score.
        score = max(0.0, 100.0 - raw)

    return round(score, 1)


def _grade_from_score(score: float, findings: list[Finding]) -> Grade:
    """Derive letter grade from numeric score, with hard gate overrides for criticals."""
    counts = _count_by_severity(findings)
    c = counts[Severity.CRITICAL]

    # Hard gates: criticals override the score-based grade.
    if c >= 2:
        return Grade.F
    if c == 1:
        # Cap at D regardless of score.
        score_grade = _score_to_letter(score)
        idx = max(GRADE_ORDER.index(score_grade), GRADE_ORDER.index(Grade.D))
        return GRADE_ORDER[idx]

    return _score_to_letter(score)


def _score_to_letter(score: float) -> Grade:
    if score >= 90:
        return Grade.A
    if score >= 75:
        return Grade.B
    if score >= 60:
        return Grade.C
    if score >= 40:
        return Grade.D
    return Grade.F


def _severity_sort_key(finding: Finding) -> int:
    order = {
        Severity.CRITICAL: 0,
        Severity.HIGH: 1,
        Severity.MEDIUM: 2,
        Severity.LOW: 3,
        Severity.INFO: 4,
    }
    return order.get(finding.severity, 5)


def grade_results(
    results: list[EvaluatorResult],
    server_meta: ServerMeta,
    total_events: int = 0,
    total_sessions: int = 0,
    tool_count: int = 0,
    call_count: int = 0,
) -> AuditReport:
    dimension_scores: list[DimensionScore] = []

    for result in results:
        counts = _count_by_severity(result.findings)
        denom_type = DIMENSION_DENOMINATOR.get(result.dimension)
        score = _score_dimension(result.findings, denom_type, tool_count, call_count)
        grade = _grade_from_score(score, result.findings)

        denom_value = 0
        if denom_type == "tools":
            denom_value = tool_count
        elif denom_type == "calls":
            denom_value = call_count

        dimension_scores.append(
            DimensionScore(
                dimension=result.dimension,
                score=score,
                grade=grade,
                critical_count=counts[Severity.CRITICAL],
                high_count=counts[Severity.HIGH],
                medium_count=counts[Severity.MEDIUM],
                low_count=counts[Severity.LOW],
                info_count=counts[Severity.INFO],
                findings=result.findings,
                checks_skipped=result.checks_skipped,
                denominator_type=denom_type,
                denominator_value=denom_value,
            )
        )

    all_findings = [f for r in results for f in r.findings]
    all_findings.sort(key=_severity_sort_key)
    top_findings = all_findings[:10]

    return AuditReport(
        server_meta=server_meta,
        dimension_scores=dimension_scores,
        top_findings=top_findings,
        total_events=total_events,
        total_sessions=total_sessions,
        tool_count=tool_count,
        call_count=call_count,
    )
