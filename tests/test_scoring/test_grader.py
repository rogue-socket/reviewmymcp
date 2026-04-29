"""Tests for the grading engine."""

from reviewmymcp.evaluators.base import EvaluatorResult, Finding, Severity, SkippedCheck
from reviewmymcp.scoring.grader import Grade, grade_results, _score_dimension, _grade_from_score

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


# --- Numeric score computation ---


class TestScoreDimension:
    def test_no_findings_scores_100(self):
        assert _score_dimension([], None, 0, 0) == 100.0

    def test_raw_dimension_deducts_directly(self):
        findings = [_finding("c.x", Severity.HIGH)]  # 10 points
        score = _score_dimension(findings, None, 0, 0)
        assert score == 90.0

    def test_raw_dimension_critical_deducts_25(self):
        findings = [_finding("c.x", Severity.CRITICAL)]
        score = _score_dimension(findings, None, 0, 0)
        assert score == 75.0

    def test_raw_dimension_floors_at_zero(self):
        findings = [_finding(f"c.{i}", Severity.CRITICAL) for i in range(5)]
        score = _score_dimension(findings, None, 0, 0)
        assert score == 0.0

    def test_tools_normalized_single_finding_many_tools(self):
        # 1 high finding across 10 tools: per_unit = 10/10 = 1, score = 100 - 4 = 96
        findings = [_finding("d.x", Severity.HIGH)]
        score = _score_dimension(findings, "tools", tool_count=10, call_count=0)
        assert score == 96.0

    def test_tools_normalized_single_finding_few_tools(self):
        # 1 high finding across 2 tools: per_unit = 10/2 = 5, score = 100 - 20 = 80
        findings = [_finding("d.x", Severity.HIGH)]
        score = _score_dimension(findings, "tools", tool_count=2, call_count=0)
        assert score == 80.0

    def test_calls_normalized(self):
        # 1 medium finding across 20 calls: per_unit = 4/20 = 0.2, score = 100 - 0.8 = 99.2
        findings = [_finding("r.x", Severity.MEDIUM)]
        score = _score_dimension(findings, "calls", tool_count=0, call_count=20)
        assert score == 99.2

    def test_zero_denominator_falls_back_to_raw(self):
        findings = [_finding("d.x", Severity.HIGH)]
        score = _score_dimension(findings, "tools", tool_count=0, call_count=0)
        assert score == 90.0  # raw fallback: 100 - 10

    def test_medium_findings_with_normalization(self):
        # 3 medium findings across 5 tools: raw = 12, per_unit = 2.4, score = 100 - 9.6 = 90.4
        findings = [_finding(f"d.{i}", Severity.MEDIUM) for i in range(3)]
        score = _score_dimension(findings, "tools", tool_count=5, call_count=0)
        assert score == 90.4


# --- Grade from score with hard gates ---


class TestGradeFromScore:
    def test_score_95_is_A(self):
        assert _grade_from_score(95.0, []) == Grade.A

    def test_score_90_is_A(self):
        assert _grade_from_score(90.0, []) == Grade.A

    def test_score_89_is_B(self):
        assert _grade_from_score(89.0, []) == Grade.B

    def test_score_75_is_B(self):
        assert _grade_from_score(75.0, []) == Grade.B

    def test_score_60_is_C(self):
        assert _grade_from_score(60.0, []) == Grade.C

    def test_score_40_is_D(self):
        assert _grade_from_score(40.0, []) == Grade.D

    def test_score_39_is_F(self):
        assert _grade_from_score(39.0, []) == Grade.F

    def test_one_critical_caps_at_D(self):
        findings = [_finding("s.x", Severity.CRITICAL)]
        # Even with a high score, one critical caps at D
        assert _grade_from_score(95.0, findings) == Grade.D

    def test_two_criticals_forces_F(self):
        findings = [_finding("s.x", Severity.CRITICAL), _finding("s.y", Severity.CRITICAL)]
        assert _grade_from_score(95.0, findings) == Grade.F

    def test_one_critical_with_low_score_stays_at_worst(self):
        findings = [_finding("s.x", Severity.CRITICAL)]
        # Score yields F, critical caps at D — F is worse, so F wins
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

    def test_normalization_with_tool_count(self):
        # 1 high finding in discoverability with 50 tools should score high
        findings = [_finding("d.x", Severity.HIGH)]
        results = [_result("discoverability", findings)]
        report = grade_results(results, make_server_meta(), tool_count=50)
        ds = report.dimension_scores[0]
        assert ds.score > 95  # per_unit = 10/50 = 0.2, score = 100 - 0.8 = 99.2
        assert ds.grade == Grade.A

    def test_normalization_with_few_tools(self):
        # Same finding with only 2 tools should score lower
        findings = [_finding("d.x", Severity.HIGH)]
        results = [_result("discoverability", findings)]
        report = grade_results(results, make_server_meta(), tool_count=2)
        ds = report.dimension_scores[0]
        assert ds.score == 80.0  # per_unit = 10/2 = 5, score = 100 - 20 = 80
        assert ds.grade == Grade.B

    def test_conformance_uses_raw_scoring(self):
        # Conformance has no normalization denominator
        findings = [_finding("c.x", Severity.HIGH)]
        results = [_result("conformance", findings)]
        report = grade_results(results, make_server_meta(), tool_count=100)
        ds = report.dimension_scores[0]
        assert ds.score == 90.0  # raw: 100 - 10, ignores tool_count

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

    def test_denominator_info_on_dimension_score(self):
        results = [_result("discoverability", [])]
        report = grade_results(results, make_server_meta(), tool_count=15)
        ds = report.dimension_scores[0]
        assert ds.denominator_type == "tools"
        assert ds.denominator_value == 15

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
        assert scores["efficiency"].score > 95  # 1 medium across 10 tools
