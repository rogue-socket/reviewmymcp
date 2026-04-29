"""Tests that SARIF output validates against the official SARIF 2.1.0 JSON schema."""

import json
from pathlib import Path

import jsonschema

from reviewmymcp.evaluators.base import EvaluatorResult, Finding, Severity, SkippedCheck
from reviewmymcp.reporting.sarif_report import render_sarif
from reviewmymcp.scoring.grader import grade_results

from tests.conftest import make_server_meta

SARIF_SCHEMA_PATH = Path(__file__).parent.parent / "fixtures" / "sarif-schema-2.1.0.json"
SARIF_SCHEMA = json.loads(SARIF_SCHEMA_PATH.read_text())


def _make_report_with_findings(findings: list[Finding], dimension: str = "security"):
    results = [
        EvaluatorResult(
            dimension=dimension,
            checks_run=[f.check_id for f in findings],
            findings=findings,
        ),
    ]
    return grade_results(results, make_server_meta(), total_events=10, total_sessions=1, tool_count=5, call_count=10)


def test_sarif_validates_with_findings():
    findings = [
        Finding(
            check_id="security.secret-leakage",
            severity=Severity.CRITICAL,
            title="Secret leaked",
            description="Connection string found in response",
            affected_entity="get_config",
            remediation="Remove secrets from responses",
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
    report = grade_results(results, make_server_meta(), total_events=50, total_sessions=3, tool_count=10, call_count=30)
    sarif = json.loads(render_sarif(report))
    jsonschema.validate(sarif, SARIF_SCHEMA)


def test_sarif_validates_empty_report():
    report = grade_results([], make_server_meta(), total_events=0, total_sessions=0, tool_count=0, call_count=0)
    sarif = json.loads(render_sarif(report))
    jsonschema.validate(sarif, SARIF_SCHEMA)


def test_sarif_validates_all_severity_levels():
    findings = [
        Finding(check_id=f"test.{sev.value}", severity=sev, title=f"{sev.value} finding",
                description="desc", affected_entity="tool", remediation="fix")
        for sev in Severity
    ]
    report = _make_report_with_findings(findings)
    sarif = json.loads(render_sarif(report))
    jsonschema.validate(sarif, SARIF_SCHEMA)
