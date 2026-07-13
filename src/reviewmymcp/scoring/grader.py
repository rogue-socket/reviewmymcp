"""Grading engine: rolls up findings into per-dimension numeric scores and letter grades."""

from __future__ import annotations

import math
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

SEVERITY_WEIGHT: dict[Severity, float] = {
    Severity.CRITICAL: 25.0,
    Severity.HIGH: 10.0,
    Severity.MEDIUM: 4.0,
    Severity.LOW: 1.0,
    Severity.INFO: 0.0,
}

# Square-root softens the deduction curve: doubling findings doesn't double impact.
# Tuned so 5 HIGH (raw=50) → ~65 (D after the 5+ HIGH hard cap),
# 5 MEDIUM (raw=20) → ~78 (B), 24 MEDIUM (raw=99) → ~50 (D).
SCORE_CURVE_FACTOR = 5.0
RAW_DEDUCTION_CAP = 100.0


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


def _resolved_severity_weights(configured_weights: dict[str, float] | None) -> dict[Severity, float]:
    weights = dict(SEVERITY_WEIGHT)
    if configured_weights:
        weights.update({Severity(name): value for name, value in configured_weights.items()})
    return weights


def _raw_deduction(findings: list[Finding], severity_weights: dict[Severity, float] = SEVERITY_WEIGHT) -> float:
    """Sum severity-weighted deductions for a set of findings."""
    return sum(severity_weights[f.severity] for f in findings)


def _score_dimension(
    findings: list[Finding],
    severity_weights: dict[Severity, float] = SEVERITY_WEIGHT,
    score_curve_factor: float = SCORE_CURVE_FACTOR,
) -> float:
    """Compute a 0-100 numeric score from severity-weighted deductions.

    Raw deductions are softened with a square root so high finding counts
    don't crater the score linearly. Raw is capped at 100 before the curve.
    """
    raw = _raw_deduction(findings, severity_weights)
    if raw == 0:
        return 100.0
    capped = min(raw, RAW_DEDUCTION_CAP)
    score = max(0.0, 100.0 - math.sqrt(capped) * score_curve_factor)
    return round(score, 1)


def _grade_from_score(score: float, findings: list[Finding]) -> Grade:
    """Derive letter grade from numeric score, with hard caps for severe findings."""
    counts = _count_by_severity(findings)
    crit = counts[Severity.CRITICAL]
    high = counts[Severity.HIGH]

    score_grade = _score_to_letter(score)

    # Hard caps escalate from the score-based grade (never improve it).
    cap: Grade | None = None
    if crit >= 2:
        return Grade.F
    if crit == 1:
        cap = Grade.D
    elif high >= 5:
        cap = Grade.D
    elif high >= 3:
        cap = Grade.C

    if cap is None:
        return score_grade
    idx = max(GRADE_ORDER.index(score_grade), GRADE_ORDER.index(cap))
    return GRADE_ORDER[idx]


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
    severity_weights: dict[str, float] | None = None,
    score_curve_factor: float = SCORE_CURVE_FACTOR,
) -> AuditReport:
    dimension_scores: list[DimensionScore] = []
    resolved_weights = _resolved_severity_weights(severity_weights)

    for result in results:
        counts = _count_by_severity(result.findings)
        score = _score_dimension(result.findings, resolved_weights, score_curve_factor)
        grade = _grade_from_score(score, result.findings)

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
