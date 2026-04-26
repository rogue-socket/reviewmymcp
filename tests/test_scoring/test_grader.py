"""Tests for the grading engine."""

from reviewmymcp.evaluators.base import EvaluatorResult, Finding, Severity
from reviewmymcp.scoring.grader import Grade, grade_results

from tests.conftest import make_server_meta


def _finding(check_id: str, severity: Severity, entity: str = "") -> Finding:
    return Finding(
        check_id=check_id,
        severity=severity,
        title=f"Test finding: {check_id}",
        description="Test",
        affected_entity=entity,
    )


def _result(dimension: str, findings: list[Finding]) -> EvaluatorResult:
    return EvaluatorResult(dimension=dimension, checks_run=[f.check_id for f in findings], findings=findings)


def test_grade_a_no_findings():
    results = [_result("efficiency", [])]
    report = grade_results(results, make_server_meta())
    assert report.dimension_scores[0].grade == Grade.A
    assert report.overall_grade == Grade.A


def test_grade_a_with_low_findings():
    results = [_result("efficiency", [_finding("e.x", Severity.LOW), _finding("e.y", Severity.INFO)])]
    report = grade_results(results, make_server_meta())
    assert report.dimension_scores[0].grade == Grade.A


def test_grade_b_with_high():
    results = [_result("security", [_finding("s.x", Severity.HIGH)])]
    report = grade_results(results, make_server_meta())
    assert report.dimension_scores[0].grade == Grade.B


def test_grade_c_many_high():
    findings = [_finding(f"s.{i}", Severity.HIGH) for i in range(3)]
    results = [_result("security", findings)]
    report = grade_results(results, make_server_meta())
    assert report.dimension_scores[0].grade == Grade.C


def test_grade_d_critical():
    results = [_result("security", [_finding("s.x", Severity.CRITICAL)])]
    report = grade_results(results, make_server_meta())
    assert report.dimension_scores[0].grade == Grade.D


def test_grade_f_multiple_critical():
    findings = [_finding("s.x", Severity.CRITICAL), _finding("s.y", Severity.CRITICAL)]
    results = [_result("security", findings)]
    report = grade_results(results, make_server_meta())
    assert report.dimension_scores[0].grade == Grade.F


def test_overall_grade_worst_dimension():
    results = [
        _result("efficiency", []),  # A
        _result("security", [_finding("s.x", Severity.CRITICAL), _finding("s.y", Severity.CRITICAL)]),  # F
    ]
    report = grade_results(results, make_server_meta())
    assert report.overall_grade == Grade.F


def test_overall_grade_uplift():
    # 7+ dimensions at A/B should uplift overall by one
    results = [_result(f"dim{i}", []) for i in range(8)]  # 8 A's
    results.append(_result("bad_dim", [_finding("b.x", Severity.HIGH)]))  # 1 B
    report = grade_results(results, make_server_meta())
    # Worst is B, 8 are A -> uplift to A
    assert report.overall_grade == Grade.A


def test_top_findings_sorted_by_severity():
    results = [
        _result("dim1", [
            _finding("d.low", Severity.LOW),
            _finding("d.critical", Severity.CRITICAL),
            _finding("d.medium", Severity.MEDIUM),
            _finding("d.high", Severity.HIGH),
        ])
    ]
    report = grade_results(results, make_server_meta())
    assert report.top_findings[0].severity == Severity.CRITICAL
    assert report.top_findings[1].severity == Severity.HIGH
    assert report.top_findings[2].severity == Severity.MEDIUM


def test_report_metadata():
    results = [_result("efficiency", [])]
    report = grade_results(results, make_server_meta(), total_events=100, total_sessions=5)
    assert report.total_events == 100
    assert report.total_sessions == 5
    assert report.server_meta.server_name == "TestServer"
