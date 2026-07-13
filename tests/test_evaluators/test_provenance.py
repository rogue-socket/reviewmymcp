"""Tests for provenance evaluators."""

from reviewmymcp.evaluators.base import EvaluatorConfig
from reviewmymcp.evaluators.provenance.checks import ProvenanceEvaluator
from tests.conftest import make_server_meta


def test_source_reachable_flags_unreachable_repository():
    meta = make_server_meta()
    meta.provenance.repository_url = "https://github.com/example/deleted"
    meta.provenance.source_reachable = False

    result = ProvenanceEvaluator().evaluate([], meta, EvaluatorConfig())
    findings = [f for f in result.findings if f.check_id == "provenance.source-reachable"]

    assert len(findings) == 1
    assert findings[0].severity.value == "medium"


def test_artifact_files_flag_missing_readme_and_declared_license():
    meta = make_server_meta()
    meta.provenance.artifact_files = ["dist/index.js", "package.json"]
    meta.provenance.license_declared = "MIT"

    result = ProvenanceEvaluator().evaluate([], meta, EvaluatorConfig())
    ids = [f.check_id for f in result.findings]

    assert "provenance.readme-in-artifact" in ids
    assert "provenance.license-in-artifact" in ids


def test_artifact_files_accept_readme_and_license():
    meta = make_server_meta()
    meta.provenance.artifact_files = ["README.md", "LICENSE", "dist/index.js"]
    meta.provenance.license_declared = "MIT"

    result = ProvenanceEvaluator().evaluate([], meta, EvaluatorConfig())
    ids = [f.check_id for f in result.findings]

    assert "provenance.readme-in-artifact" not in ids
    assert "provenance.license-in-artifact" not in ids


def test_maintenance_recency_severity_thresholds():
    meta = make_server_meta()
    meta.provenance.last_activity_days = 400

    result = ProvenanceEvaluator().evaluate([], meta, EvaluatorConfig())
    findings = [f for f in result.findings if f.check_id == "provenance.maintenance-recency"]

    assert len(findings) == 1
    assert findings[0].severity.value == "low"


def test_version_churn_flags_multiple_versions_within_one_hour():
    meta = make_server_meta()
    meta.provenance.version_publish_times = [
        "2026-01-01T10:00:00Z",
        "2026-01-01T10:20:00Z",
        "2026-02-01T10:20:00Z",
    ]

    result = ProvenanceEvaluator().evaluate([], meta, EvaluatorConfig())
    findings = [f for f in result.findings if f.check_id == "provenance.version-churn"]

    assert len(findings) == 1
    assert findings[0].severity.value == "info"


def test_checks_run():
    result = ProvenanceEvaluator().evaluate([], make_server_meta(), EvaluatorConfig())
    assert "provenance.source-reachable" in result.checks_run
    assert "provenance.version-churn" in result.checks_run
