"""Diff two audit reports to detect regressions and improvements."""

from __future__ import annotations

from pydantic import BaseModel, Field

from reviewmymcp.evaluators.base import Finding, Severity
from reviewmymcp.scoring.grader import AuditReport, Grade


class FindingDiff(BaseModel):
    finding: Finding
    status: str  # "new" | "resolved" | "unchanged"


class DimensionDiff(BaseModel):
    dimension: str
    baseline_grade: Grade
    current_grade: Grade
    is_regression: bool = False
    is_improvement: bool = False


class DiffReport(BaseModel):
    baseline_grade: Grade
    current_grade: Grade
    dimension_diffs: list[DimensionDiff] = Field(default_factory=list)
    new_findings: list[Finding] = Field(default_factory=list)
    resolved_findings: list[Finding] = Field(default_factory=list)
    unchanged_findings: list[Finding] = Field(default_factory=list)
    has_regressions: bool = False


def _finding_key(f: Finding) -> str:
    return f"{f.check_id}::{f.affected_entity}"


GRADE_ORDER = [Grade.A, Grade.B, Grade.C, Grade.D, Grade.F]


def diff_reports(baseline: AuditReport, current: AuditReport) -> DiffReport:
    baseline_dims = {ds.dimension: ds for ds in baseline.dimension_scores}
    current_dims = {ds.dimension: ds for ds in current.dimension_scores}

    all_dimensions = sorted(set(baseline_dims) | set(current_dims))
    dimension_diffs: list[DimensionDiff] = []
    for dim in all_dimensions:
        bg = baseline_dims[dim].grade if dim in baseline_dims else Grade.A
        cg = current_dims[dim].grade if dim in current_dims else Grade.A
        dimension_diffs.append(
            DimensionDiff(
                dimension=dim,
                baseline_grade=bg,
                current_grade=cg,
                is_regression=GRADE_ORDER.index(cg) > GRADE_ORDER.index(bg),
                is_improvement=GRADE_ORDER.index(cg) < GRADE_ORDER.index(bg),
            )
        )

    baseline_findings: dict[str, Finding] = {}
    for ds in baseline.dimension_scores:
        for f in ds.findings:
            baseline_findings[_finding_key(f)] = f

    current_findings: dict[str, Finding] = {}
    for ds in current.dimension_scores:
        for f in ds.findings:
            current_findings[_finding_key(f)] = f

    baseline_keys = set(baseline_findings)
    current_keys = set(current_findings)

    new_findings = [current_findings[k] for k in sorted(current_keys - baseline_keys)]
    resolved_findings = [baseline_findings[k] for k in sorted(baseline_keys - current_keys)]
    unchanged_findings = [current_findings[k] for k in sorted(baseline_keys & current_keys)]

    high_or_above = {Severity.CRITICAL, Severity.HIGH}
    has_regressions = any(f.severity in high_or_above for f in new_findings)

    return DiffReport(
        baseline_grade=baseline.overall_grade,
        current_grade=current.overall_grade,
        dimension_diffs=dimension_diffs,
        new_findings=new_findings,
        resolved_findings=resolved_findings,
        unchanged_findings=unchanged_findings,
        has_regressions=has_regressions,
    )
