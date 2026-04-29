"""Tests for report diffing."""

from reviewmymcp.evaluators.base import EvaluatorResult, Finding, Severity
from reviewmymcp.scoring.differ import diff_reports
from reviewmymcp.scoring.grader import Grade, grade_results

from tests.conftest import make_server_meta


def _finding(check_id: str, severity: Severity, entity: str = "tool") -> Finding:
    return Finding(check_id=check_id, severity=severity, title="Test", description="Test", affected_entity=entity)


def _result(dimension: str, findings: list[Finding]) -> EvaluatorResult:
    return EvaluatorResult(dimension=dimension, checks_run=[], findings=findings)


def _make_report(dim_findings: dict[str, list[Finding]]):
    results = [_result(dim, findings) for dim, findings in dim_findings.items()]
    return grade_results(results, make_server_meta())


def test_diff_no_changes():
    report = _make_report({"efficiency": [_finding("e.x", Severity.MEDIUM)]})
    diff = diff_reports(report, report)
    assert len(diff.new_findings) == 0
    assert len(diff.resolved_findings) == 0
    assert diff.has_regressions is False


def test_diff_new_finding():
    baseline = _make_report({"efficiency": []})
    current = _make_report({"efficiency": [_finding("e.x", Severity.HIGH)]})
    diff = diff_reports(baseline, current)
    assert len(diff.new_findings) == 1
    assert diff.new_findings[0].check_id == "e.x"
    assert diff.has_regressions is True


def test_diff_resolved_finding():
    baseline = _make_report({"efficiency": [_finding("e.x", Severity.HIGH)]})
    current = _make_report({"efficiency": []})
    diff = diff_reports(baseline, current)
    assert len(diff.resolved_findings) == 1
    assert diff.has_regressions is False


def test_diff_regression_only_for_high_plus():
    baseline = _make_report({"efficiency": []})
    current = _make_report({"efficiency": [_finding("e.x", Severity.LOW)]})
    diff = diff_reports(baseline, current)
    assert len(diff.new_findings) == 1
    assert diff.has_regressions is False  # LOW is not high enough


def test_diff_dimension_regression():
    baseline = _make_report({"efficiency": [], "security": []})
    current = _make_report({
        "efficiency": [],
        "security": [_finding("s.x", Severity.CRITICAL), _finding("s.y", Severity.CRITICAL)],
    })
    diff = diff_reports(baseline, current)
    sec_diff = next(d for d in diff.dimension_diffs if d.dimension == "security")
    assert sec_diff.is_regression is True
    assert sec_diff.baseline_grade == Grade.A
    assert sec_diff.current_grade == Grade.F


def test_diff_dimension_improvement():
    baseline = _make_report({"security": [_finding("s.x", Severity.CRITICAL, "t1"), _finding("s.y", Severity.CRITICAL, "t2")]})
    current = _make_report({"security": []})
    diff = diff_reports(baseline, current)
    sec_diff = next(d for d in diff.dimension_diffs if d.dimension == "security")
    assert sec_diff.is_improvement is True


def test_diff_has_scores():
    baseline = _make_report({"efficiency": [_finding("e.x", Severity.HIGH)]})
    current = _make_report({"efficiency": []})
    diff = diff_reports(baseline, current)
    eff_diff = next(d for d in diff.dimension_diffs if d.dimension == "efficiency")
    assert eff_diff.baseline_score < 100.0
    assert eff_diff.current_score == 100.0
