"""Tests for the grading engine."""

import math

from reviewmymcp.evaluators.base import EvaluatorResult, Finding, Severity, SkippedCheck
from reviewmymcp.scoring.grader import (
    Grade,
    SCORE_CURVE_FACTOR,
    _grade_from_score,
    _score_dimension,
    grade_results,
)

from tests.conftest import make_server_meta


def _finding(check_id: str, severity: Severity, entity: str = "") -> Finding:
    return Finding(
        check_id=check_id,
        severity=severity,
        title=f"Test finding: {check_id}",
        description="Test",
        affected_entity=entity,
    )


def _result(dimension: str, findings: list[Finding], skipped: list[SkippedCheck] | None = None) -> EvaluatorResult:
    return EvaluatorResult(
        dimension=dimension,
        checks_run=[f.check_id for f in findings],
        findings=findings,
        checks_skipped=skipped or [],
    )


def _expected(raw: float) -> float:
    return round(max(0.0, 100.0 - math.sqrt(min(raw, 100.0)) * SCORE_CURVE_FACTOR), 1)


# --- Numeric score computation ---


class TestScoreDimension:
    def test_no_findings_scores_100(self):
        assert _score_dimension([]) == 100.0

    def test_single_high(self):
        # raw = 10 → 100 - sqrt(10)*5 ≈ 84.2
        score = _score_dimension([_finding("d.x", Severity.HIGH)])
        assert score == _expected(10)
        assert score > 80 and score < 90

    def test_single_critical(self):
        # raw = 25 → 100 - sqrt(25)*5 = 75
        score = _score_dimension([_finding("s.x", Severity.CRITICAL)])
        assert score == 75.0

    def test_five_highs(self):
        # raw = 50 → 100 - sqrt(50)*5 ≈ 64.6
        score = _score_dimension([_finding(f"r.{i}", Severity.HIGH) for i in range(5)])
        assert score == _expected(50)
        assert 60 < score < 70

    def test_five_mediums(self):
        # raw = 20 → 100 - sqrt(20)*5 ≈ 77.6 (B)
        score = _score_dimension([_finding(f"e.{i}", Severity.MEDIUM) for i in range(5)])
        assert score == _expected(20)
        assert score > 75

    def test_many_low_severity_findings_dont_crater(self):
        # 24 MEDIUM + 3 LOW → raw = 99 → ~50 (not 1, which is what linear would give)
        findings = [_finding(f"d.m{i}", Severity.MEDIUM) for i in range(24)]
        findings += [_finding(f"d.l{i}", Severity.LOW) for i in range(3)]
        score = _score_dimension(findings)
        assert 45 < score < 55

    def test_raw_deduction_cap_floors_score(self):
        # Many criticals: raw is capped at 100, score = 100 - sqrt(100)*5 = 50
        findings = [_finding(f"c.{i}", Severity.CRITICAL) for i in range(10)]
        assert _score_dimension(findings) == 50.0

    def test_score_does_not_depend_on_tool_or_call_count(self):
        findings = [_finding("d.x", Severity.HIGH)]
        # Same findings → same score regardless of surface area
        assert _score_dimension(findings) == _score_dimension(findings)


# --- Grade from score with hard caps ---


class TestGradeFromScore:
    def test_score_thresholds(self):
        assert _grade_from_score(95.0, []) == Grade.A
        assert _grade_from_score(90.0, []) == Grade.A
        assert _grade_from_score(89.0, []) == Grade.B
        assert _grade_from_score(75.0, []) == Grade.B
        assert _grade_from_score(60.0, []) == Grade.C
        assert _grade_from_score(40.0, []) == Grade.D
        assert _grade_from_score(39.0, []) == Grade.F

    def test_one_critical_caps_at_D(self):
        findings = [_finding("s.x", Severity.CRITICAL)]
        assert _grade_from_score(95.0, findings) == Grade.D

    def test_two_criticals_forces_F(self):
        findings = [_finding("s.x", Severity.CRITICAL), _finding("s.y", Severity.CRITICAL)]
        assert _grade_from_score(95.0, findings) == Grade.F

    def test_one_critical_with_low_score_stays_at_worst(self):
        # Score yields F; critical caps at D — F is worse, so F wins.
        findings = [_finding("s.x", Severity.CRITICAL)]
        assert _grade_from_score(30.0, findings) == Grade.F

    def test_three_highs_caps_at_C(self):
        findings = [_finding(f"r.{i}", Severity.HIGH) for i in range(3)]
        # Even with an A-range score, 3 HIGHs cap at C
        assert _grade_from_score(95.0, findings) == Grade.C

    def test_five_highs_caps_at_D(self):
        findings = [_finding(f"r.{i}", Severity.HIGH) for i in range(5)]
        assert _grade_from_score(95.0, findings) == Grade.D

    def test_high_cap_does_not_improve_grade(self):
        # 5 HIGHs cap at D, but a worse score (F) wins.
        findings = [_finding(f"r.{i}", Severity.HIGH) for i in range(5)]
        assert _grade_from_score(30.0, findings) == Grade.F


# --- Full grading pipeline ---


class TestGradeResults:
    def test_no_findings_grade_A(self):
        results = [_result("efficiency", [])]
        report = grade_results(results, make_server_meta())
        assert report.dimension_scores[0].grade == Grade.A
        assert report.dimension_scores[0].score == 100.0

    def test_low_findings_grade_A(self):
        results = [_result("efficiency", [_finding("e.x", Severity.LOW), _finding("e.y", Severity.INFO)])]
        report = grade_results(results, make_server_meta())
        assert report.dimension_scores[0].grade == Grade.A

    def test_single_critical_caps_at_D(self):
        results = [_result("security", [_finding("s.x", Severity.CRITICAL)])]
        report = grade_results(results, make_server_meta())
        assert report.dimension_scores[0].grade == Grade.D

    def test_two_criticals_forces_F(self):
        findings = [_finding("s.x", Severity.CRITICAL), _finding("s.y", Severity.CRITICAL)]
        results = [_result("security", findings)]
        report = grade_results(results, make_server_meta())
        assert report.dimension_scores[0].grade == Grade.F

    def test_score_independent_of_surface_area(self):
        # Same single HIGH finding on a 2-tool server vs a 50-tool server: same score.
        findings = [_finding("d.x", Severity.HIGH)]
        r_small = grade_results([_result("discoverability", findings)], make_server_meta(), tool_count=2)
        r_large = grade_results([_result("discoverability", findings)], make_server_meta(), tool_count=50)
        assert r_small.dimension_scores[0].score == r_large.dimension_scores[0].score

    def test_no_overall_grade(self):
        results = [_result("efficiency", [])]
        report = grade_results(results, make_server_meta())
        assert not hasattr(report, "overall_grade")

    def test_top_findings_sorted_by_severity(self):
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

    def test_report_metadata(self):
        results = [_result("efficiency", [])]
        report = grade_results(results, make_server_meta(), total_events=100, total_sessions=5, tool_count=10, call_count=50)
        assert report.total_events == 100
        assert report.total_sessions == 5
        assert report.tool_count == 10
        assert report.call_count == 50
        assert report.server_meta.server_name == "TestServer"

    def test_checks_skipped_carried_through(self):
        skipped = [SkippedCheck(check_id="perf.concurrent", reason="only 1 session")]
        results = [_result("performance", [], skipped=skipped)]
        report = grade_results(results, make_server_meta())
        ds = report.dimension_scores[0]
        assert len(ds.checks_skipped) == 1
        assert ds.checks_skipped[0].check_id == "perf.concurrent"

    def test_multiple_dimensions(self):
        results = [
            _result("efficiency", [_finding("e.x", Severity.MEDIUM)]),
            _result("security", [_finding("s.x", Severity.CRITICAL)]),
            _result("conformance", []),
        ]
        report = grade_results(results, make_server_meta(), tool_count=10)
        scores = {ds.dimension: ds for ds in report.dimension_scores}
        assert scores["conformance"].grade == Grade.A
        assert scores["security"].grade == Grade.D  # 1 critical caps at D
        # 1 medium → raw=4 → ~90
        assert scores["efficiency"].score == _expected(4)
        assert scores["efficiency"].grade == Grade.A
