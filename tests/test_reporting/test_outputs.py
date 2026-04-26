"""Tests for report output formats."""

import json

from reviewmymcp.evaluators.base import EvaluatorResult, Finding, Severity
from reviewmymcp.reporting.html_report import render_html
from reviewmymcp.reporting.json_report import render_json
from reviewmymcp.reporting.sarif_report import render_sarif
from reviewmymcp.scoring.grader import grade_results

from tests.conftest import make_server_meta


def _make_report():
    findings = [
        Finding(
            check_id="security.secret-leakage",
            severity=Severity.CRITICAL,
            title="Secret leaked",
            description="Found a secret",
            affected_entity="get_config",
            remediation="Don't leak secrets",
        ),
        Finding(
            check_id="efficiency.description-bloat",
            severity=Severity.HIGH,
            title="Description too long",
            description="Tool description is 2000 tokens",
            affected_entity="search_tool",
        ),
    ]
    results = [
        EvaluatorResult(dimension="security", checks_run=["security.secret-leakage"], findings=[findings[0]]),
        EvaluatorResult(dimension="efficiency", checks_run=["efficiency.description-bloat"], findings=[findings[1]]),
    ]
    return grade_results(results, make_server_meta(), total_events=50, total_sessions=3)


def test_json_output_valid():
    report = _make_report()
    output = render_json(report)
    parsed = json.loads(output)
    assert parsed["overall_grade"] == "D"
    assert len(parsed["dimension_scores"]) == 2
    assert len(parsed["top_findings"]) == 2
    assert parsed["total_events"] == 50


def test_json_roundtrip():
    from reviewmymcp.scoring.grader import AuditReport

    report = _make_report()
    output = render_json(report)
    restored = AuditReport.model_validate_json(output)
    assert restored.overall_grade == report.overall_grade
    assert len(restored.dimension_scores) == len(report.dimension_scores)


def test_html_output():
    report = _make_report()
    html = render_html(report)
    assert "<html" in html
    assert "TestServer" in html
    assert "CRITICAL" in html
    assert "secret-leakage" in html
    assert "description-bloat" in html


def test_sarif_output_valid():
    report = _make_report()
    output = render_sarif(report)
    parsed = json.loads(output)
    assert parsed["version"] == "2.1.0"
    assert len(parsed["runs"]) == 1
    run = parsed["runs"][0]
    assert run["tool"]["driver"]["name"] == "reviewmymcp"
    assert len(run["results"]) == 2


def test_sarif_severity_mapping():
    report = _make_report()
    output = render_sarif(report)
    parsed = json.loads(output)
    results = parsed["runs"][0]["results"]
    critical = [r for r in results if r["properties"]["severity"] == "critical"]
    assert len(critical) == 1
    assert critical[0]["level"] == "error"
