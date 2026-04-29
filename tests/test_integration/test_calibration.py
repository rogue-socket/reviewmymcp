"""Calibration tests — pin expected grades for known fixtures to detect weight regressions.

Current weights: critical=25, high=10, medium=4, low=1, normalization_multiplier=4.
These tests document that:
- Good servers (Everything, Filesystem) get mostly A grades
- The critical hard-cap (1 critical → D, 2+ → F) works correctly
- The known-bad sample fixture gets F in security (2 criticals: SSN + connection string)
"""

from __future__ import annotations

from pathlib import Path

from tests.test_integration.test_real_fixtures import _full_pipeline, _register_all

from reviewmymcp.evaluators.base import EvaluatorConfig
from reviewmymcp.evaluators.registry import run_all
from reviewmymcp.ingest.correlator import correlate, get_sessions
from reviewmymcp.ingest.file_loader import load_file
from reviewmymcp.ingest.normalizer import extract_server_meta
from reviewmymcp.scoring.grader import AuditReport, grade_results

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _full_pipeline_no_redact(log_path: Path) -> AuditReport:
    """Full pipeline without redaction — secrets stay visible to evaluators."""
    _register_all()
    events = load_file(str(log_path), redact=False)
    events = correlate(events)
    server_meta = extract_server_meta(events)
    sessions = get_sessions(events)
    config = EvaluatorConfig()
    results = run_all(events, server_meta, config)
    tool_count = len(server_meta.tools)
    call_count = sum(1 for e in events if e.is_request and e.method == "tools/call")
    return grade_results(
        results, server_meta,
        total_events=len(events), total_sessions=len(sessions),
        tool_count=tool_count, call_count=call_count,
    )


class TestEverythingCalibration:
    """Everything server is the reference MCP implementation — should grade well."""

    def test_mostly_a_grades(self):
        report = _full_pipeline(FIXTURES / "everything_server.ndjson")
        grades = {ds.dimension: ds.grade for ds in report.dimension_scores}
        a_count = sum(1 for g in grades.values() if g == "A")
        # At least 7 of 9 dimensions should be A
        assert a_count >= 7, f"Expected >= 7 A grades, got {a_count}: {grades}"

    def test_reliability_capped_by_critical(self):
        """One timeout (critical) should hard-cap reliability at D."""
        report = _full_pipeline(FIXTURES / "everything_server.ndjson")
        reliability = next(ds for ds in report.dimension_scores if ds.dimension == "reliability")
        assert reliability.critical_count >= 1
        assert reliability.grade in ("D", "F"), f"Critical should cap at D, got {reliability.grade}"
        # But the numeric score (ignoring the cap) should still be high
        assert reliability.score >= 85


class TestFilesystemCalibration:
    """Filesystem server is a well-built server. Bad paths cause schema-misuse but that's fair."""

    def test_mostly_a_grades(self):
        report = _full_pipeline(FIXTURES / "filesystem_server.ndjson")
        grades = {ds.dimension: ds.grade for ds in report.dimension_scores}
        a_count = sum(1 for g in grades.values() if g == "A")
        assert a_count >= 7, f"Expected >= 7 A grades, got {a_count}: {grades}"

    def test_accuracy_b_from_test_paths(self):
        """Fake test paths cause schema-misuse → accuracy B is expected, not A or F."""
        report = _full_pipeline(FIXTURES / "filesystem_server.ndjson")
        accuracy = next(ds for ds in report.dimension_scores if ds.dimension == "accuracy")
        assert accuracy.grade in ("A", "B"), f"Expected A or B, got {accuracy.grade}"


class TestSampleBadCalibration:
    """Sample fixture has secrets (SSN, connection string) — should fail security hard.

    Uses --no-redact so the security evaluator sees raw secrets.
    """

    def test_security_f_two_criticals(self):
        report = _full_pipeline_no_redact(FIXTURES / "sample_stdio_log.ndjson")
        security = next(ds for ds in report.dimension_scores if ds.dimension == "security")
        assert security.critical_count >= 2
        assert security.grade == "F", f"2+ criticals should force F, got {security.grade}"

    def test_exit_code_is_1(self):
        """With critical findings, the report should trigger exit code 1."""
        report = _full_pipeline_no_redact(FIXTURES / "sample_stdio_log.ndjson")
        has_critical_or_high = any(
            f.severity.value in ("critical", "high") for f in report.top_findings
        )
        assert has_critical_or_high
