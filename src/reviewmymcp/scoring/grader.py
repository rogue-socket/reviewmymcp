"""Grading engine: rolls up findings into dimension grades and an overall server grade."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from reviewmymcp.evaluators.base import EvaluatorResult, Finding, Severity
from reviewmymcp.ingest.schema import ServerMeta


class Grade(StrEnum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    F = "F"


GRADE_ORDER = [Grade.A, Grade.B, Grade.C, Grade.D, Grade.F]


class DimensionScore(BaseModel):
    dimension: str
    grade: Grade
    critical_count: int = 0
    high_count: int = 0
    medium_count: int = 0
    low_count: int = 0
    info_count: int = 0
    findings: list[Finding] = Field(default_factory=list)


class AuditReport(BaseModel):
    server_meta: ServerMeta
    overall_grade: Grade
    dimension_scores: list[DimensionScore] = Field(default_factory=list)
    top_findings: list[Finding] = Field(default_factory=list)
    total_events: int = 0
    total_sessions: int = 0
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


def _count_by_severity(findings: list[Finding]) -> dict[Severity, int]:
    counts: dict[Severity, int] = {s: 0 for s in Severity}
    for f in findings:
        counts[f.severity] += 1
    return counts


def _grade_dimension(findings: list[Finding]) -> Grade:
    counts = _count_by_severity(findings)
    c = counts[Severity.CRITICAL]
    h = counts[Severity.HIGH]
    m = counts[Severity.MEDIUM]

    if c >= 2:
        return Grade.F
    if c == 1 or h >= 5:
        return Grade.D
    if h >= 3 or m >= 5:
        return Grade.C
    if h > 0 and h <= 2:
        return Grade.B
    if m > 2:
        return Grade.B
    return Grade.A


def _uplift(grade: Grade) -> Grade:
    idx = GRADE_ORDER.index(grade)
    return GRADE_ORDER[max(0, idx - 1)]


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
) -> AuditReport:
    dimension_scores: list[DimensionScore] = []

    for result in results:
        counts = _count_by_severity(result.findings)
        grade = _grade_dimension(result.findings)
        dimension_scores.append(
            DimensionScore(
                dimension=result.dimension,
                grade=grade,
                critical_count=counts[Severity.CRITICAL],
                high_count=counts[Severity.HIGH],
                medium_count=counts[Severity.MEDIUM],
                low_count=counts[Severity.LOW],
                info_count=counts[Severity.INFO],
                findings=result.findings,
            )
        )

    if not dimension_scores:
        overall = Grade.A
    else:
        worst = max(dimension_scores, key=lambda ds: GRADE_ORDER.index(ds.grade))
        overall = worst.grade
        ab_count = sum(1 for ds in dimension_scores if ds.grade in (Grade.A, Grade.B))
        if ab_count >= 7:
            overall = _uplift(overall)

    all_findings = [f for r in results for f in r.findings]
    all_findings.sort(key=_severity_sort_key)
    top_findings = all_findings[:10]

    return AuditReport(
        server_meta=server_meta,
        overall_grade=overall,
        dimension_scores=dimension_scores,
        top_findings=top_findings,
        total_events=total_events,
        total_sessions=total_sessions,
    )
