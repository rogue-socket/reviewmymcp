"""Tests for report output formats."""

import json

from reviewmymcp.evaluators.base import EvaluatorResult, Finding, Severity, SkippedCheck
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
    skipped = [SkippedCheck(check_id="security.auth-flow", reason="no HTTP traffic")]
    results = [
        EvaluatorResult(
            dimension="security",
            checks_run=["security.secret-leakage"],
            findings=[findings[0]],
            checks_skipped=skipped,
        ),
        EvaluatorResult(dimension="efficiency", checks_run=["efficiency.description-bloat"], findings=[findings[1]]),
    ]
    return grade_results(results, make_server_meta(), total_events=50, total_sessions=3, tool_count=10, call_count=30)


def test_json_output_valid():
    report = _make_report()
    output = render_json(report)
    parsed = json.loads(output)
    assert "overall_grade" not in parsed
    assert len(parsed["dimension_scores"]) == 2
    assert len(parsed["top_findings"]) == 2
    assert parsed["total_events"] == 50
    assert parsed["tool_count"] == 10
    assert parsed["call_count"] == 30


def test_json_includes_runtime_metadata():
    report = _make_report()
    report.server_meta.runtime.reproducible_command = "npx -y ddg-mcp-search@1.1.0"
    report.server_meta.runtime.package_name = "ddg-mcp-search"
    report.server_meta.runtime.package_version = "1.1.0"

    parsed = json.loads(render_json(report))

    assert parsed["server_meta"]["runtime"]["reproducible_command"] == "npx -y ddg-mcp-search@1.1.0"
    assert parsed["server_meta"]["runtime"]["package_version"] == "1.1.0"


def test_json_dimension_has_score():
    report = _make_report()
    output = render_json(report)
    parsed = json.loads(output)
    for ds in parsed["dimension_scores"]:
        assert "score" in ds
        assert isinstance(ds["score"], (int, float))
        assert "grade" in ds


def test_json_dimension_has_checks_skipped():
    report = _make_report()
    output = render_json(report)
    parsed = json.loads(output)
    sec = [ds for ds in parsed["dimension_scores"] if ds["dimension"] == "security"][0]
    assert len(sec["checks_skipped"]) == 1
    assert sec["checks_skipped"][0]["check_id"] == "security.auth-flow"


def test_json_roundtrip():
    from reviewmymcp.scoring.grader import AuditReport

    report = _make_report()
    output = render_json(report)
    restored = AuditReport.model_validate_json(output)
    assert len(restored.dimension_scores) == len(report.dimension_scores)
    for ds in restored.dimension_scores:
        assert isinstance(ds.score, float)


def test_html_output():
    report = _make_report()
    html = render_html(report)
    assert "<html" in html
    assert "TestServer" in html
    assert "CRITICAL" in html
    assert "secret-leakage" in html
    assert "description-bloat" in html


def test_html_contains_scores():
    report = _make_report()
    html = render_html(report)
    # Should contain numeric scores
    for ds in report.dimension_scores:
        assert f"{ds.score:.0f}" in html


def test_html_contains_skipped_checks():
    report = _make_report()
    html = render_html(report)
    assert "security.auth-flow" in html
    assert "no HTTP traffic" in html


def test_html_includes_runtime_metadata():
    report = _make_report()
    report.server_meta.runtime.reproducible_command = "npx -y ddg-mcp-search@1.1.0"
    report.server_meta.runtime.package_name = "ddg-mcp-search"
    report.server_meta.runtime.package_version = "1.1.0"

    html = render_html(report)

    assert "npx -y ddg-mcp-search@1.1.0" in html
    assert "ddg-mcp-search@1.1.0" in html


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
