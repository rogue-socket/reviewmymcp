"""Integration tests — run the full audit pipeline against real MCP server fixtures."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
from click.testing import CliRunner

from reviewmymcp.cli import cli
from reviewmymcp.evaluators.base import EvaluatorConfig
from reviewmymcp.evaluators.registry import register, run_all
from reviewmymcp.ingest.correlator import correlate, get_sessions
from reviewmymcp.ingest.file_loader import load_file
from reviewmymcp.ingest.normalizer import extract_server_meta
from reviewmymcp.reporting.sarif_report import render_sarif
from reviewmymcp.scoring.grader import AuditReport, grade_results

FIXTURES = Path(__file__).parent.parent / "fixtures"
EVERYTHING_LOG = FIXTURES / "everything_server.ndjson"
FILESYSTEM_LOG = FIXTURES / "filesystem_server.ndjson"
SARIF_SCHEMA = json.loads((FIXTURES / "sarif-schema-2.1.0.json").read_text())


def _register_all():
    """Ensure evaluators are registered (idempotent)."""
    from reviewmymcp.evaluators.accuracy.checks import AccuracyEvaluator
    from reviewmymcp.evaluators.compliance.checks import ComplianceEvaluator
    from reviewmymcp.evaluators.composability.checks import ComposabilityEvaluator
    from reviewmymcp.evaluators.conformance.checks import ConformanceEvaluator
    from reviewmymcp.evaluators.discoverability.checks import DiscoverabilityEvaluator
    from reviewmymcp.evaluators.efficiency.checks import EfficiencyEvaluator
    from reviewmymcp.evaluators.performance.checks import PerformanceEvaluator
    from reviewmymcp.evaluators.provenance.checks import ProvenanceEvaluator
    from reviewmymcp.evaluators.reliability.checks import ReliabilityEvaluator
    from reviewmymcp.evaluators.security.checks import SecurityEvaluator

    for cls in [
        EfficiencyEvaluator,
        AccuracyEvaluator,
        DiscoverabilityEvaluator,
        ComposabilityEvaluator,
        ReliabilityEvaluator,
        SecurityEvaluator,
        ComplianceEvaluator,
        ConformanceEvaluator,
        PerformanceEvaluator,
        ProvenanceEvaluator,
    ]:
        try:
            register(cls())
        except ValueError:
            pass


def _full_pipeline(log_path: Path) -> AuditReport:
    """Run the complete audit pipeline on a fixture file."""
    _register_all()
    events = load_file(str(log_path), redact=True)
    events = correlate(events)
    server_meta = extract_server_meta(events)
    sessions = get_sessions(events)
    config = EvaluatorConfig()
    results = run_all(events, server_meta, config)
    tool_count = len(server_meta.tools)
    call_count = sum(1 for e in events if e.is_request and e.method == "tools/call")
    return grade_results(
        results,
        server_meta,
        total_events=len(events),
        total_sessions=len(sessions),
        tool_count=tool_count,
        call_count=call_count,
    )


# ── Everything Server ──────────────────────────────────────────────


class TestEverythingServer:
    """Integration tests against the MCP Everything server."""

    def test_pipeline_completes(self):
        report = _full_pipeline(EVERYTHING_LOG)
        assert report.tool_count == 15
        assert report.call_count == 20
        assert report.total_events == 68

    def test_all_10_dimensions_scored(self):
        report = _full_pipeline(EVERYTHING_LOG)
        dims = {ds.dimension for ds in report.dimension_scores}
        expected = {
            "efficiency",
            "accuracy",
            "discoverability",
            "composability",
            "reliability",
            "security",
            "compliance",
            "conformance",
            "performance",
            "provenance",
        }
        assert dims == expected

    def test_scores_are_valid(self):
        report = _full_pipeline(EVERYTHING_LOG)
        for ds in report.dimension_scores:
            assert 0 <= ds.score <= 100
            assert ds.grade in ("A", "B", "C", "D", "F")

    def test_server_meta_populated(self):
        report = _full_pipeline(EVERYTHING_LOG)
        assert report.server_meta.server_name == "mcp-servers/everything"
        assert len(report.server_meta.tools) == 15

    def test_findings_have_required_fields(self):
        report = _full_pipeline(EVERYTHING_LOG)
        for f in report.top_findings:
            assert f.check_id
            assert f.severity
            assert f.title
            # affected_entity may be empty for server-level findings (e.g. timeout-behavior)

    def test_checks_skipped_populated(self):
        """Some checks should be skipped due to insufficient data."""
        report = _full_pipeline(EVERYTHING_LOG)
        total_skipped = sum(len(ds.checks_skipped) for ds in report.dimension_scores)
        assert total_skipped > 0, "Expected some checks to be skipped with limited fixture data"

    def test_sarif_validates_against_schema(self):
        report = _full_pipeline(EVERYTHING_LOG)
        sarif = json.loads(render_sarif(report))
        jsonschema.validate(sarif, SARIF_SCHEMA)

    def test_json_roundtrip(self):
        report = _full_pipeline(EVERYTHING_LOG)
        serialized = report.model_dump_json()
        restored = AuditReport.model_validate_json(serialized)
        assert len(restored.dimension_scores) == len(report.dimension_scores)
        for orig, rest in zip(report.dimension_scores, restored.dimension_scores):
            assert orig.dimension == rest.dimension
            assert abs(orig.score - rest.score) < 0.01


# ── Filesystem Server ─────────────────────────────────────────────


class TestFilesystemServer:
    """Integration tests against the MCP Filesystem server."""

    def test_pipeline_completes(self):
        report = _full_pipeline(FILESYSTEM_LOG)
        assert report.tool_count == 14
        assert report.call_count == 19
        assert report.total_events == 44

    def test_all_10_dimensions_scored(self):
        report = _full_pipeline(FILESYSTEM_LOG)
        dims = {ds.dimension for ds in report.dimension_scores}
        assert len(dims) == 10
        assert "provenance" in dims

    def test_server_meta_populated(self):
        report = _full_pipeline(FILESYSTEM_LOG)
        assert "filesystem" in report.server_meta.server_name.lower()
        tool_names = {t.name for t in report.server_meta.tools}
        assert "read_file" in tool_names
        assert "write_file" in tool_names
        assert "list_directory" in tool_names

    def test_accuracy_findings_for_bad_paths(self):
        """Our test args use fake paths — filesystem server rejects them. Schema-misuse should fire."""
        report = _full_pipeline(FILESYSTEM_LOG)
        accuracy = next(ds for ds in report.dimension_scores if ds.dimension == "accuracy")
        schema_misuse = [f for f in accuracy.findings if f.check_id == "accuracy.schema-misuse"]
        assert len(schema_misuse) > 0, "Expected schema-misuse findings from invalid test paths"

    def test_sarif_validates(self):
        report = _full_pipeline(FILESYSTEM_LOG)
        sarif = json.loads(render_sarif(report))
        jsonschema.validate(sarif, SARIF_SCHEMA)


# ── Correlation ────────────────────────────────────────────────────


class TestCorrelation:
    """Test request-response correlation on real data."""

    def test_everything_correlation(self):
        events = load_file(str(EVERYTHING_LOG), redact=True)
        events = correlate(events)
        correlated = [e for e in events if e.is_response and e.request_event_id and e.latency_ms is not None]
        assert len(correlated) > 0, "Expected correlated responses"
        for r in correlated:
            assert r.latency_ms >= 0

    def test_filesystem_correlation(self):
        events = load_file(str(FILESYSTEM_LOG), redact=True)
        events = correlate(events)
        correlated = [e for e in events if e.is_response and e.request_event_id and e.latency_ms is not None]
        assert len(correlated) > 0
        for r in correlated:
            assert r.latency_ms >= 0


# ── CLI replay ─────────────────────────────────────────────────────


class TestReplayCli:
    """Test the replay CLI command against real fixtures."""

    def test_replay_everything_terminal(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["replay", str(EVERYTHING_LOG), "--no-llm-judges"])
        assert result.exit_code in (0, 1)
        assert "Dimension Scores" in result.output

    def test_replay_filesystem_json(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["replay", str(FILESYSTEM_LOG), "--output", "json", "--no-llm-judges"])
        assert result.exit_code in (0, 1)
        parsed = json.loads(result.output)
        assert parsed["tool_count"] == 14

    def test_replay_everything_sarif(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["replay", str(EVERYTHING_LOG), "--output", "sarif", "--no-llm-judges"])
        assert result.exit_code in (0, 1)
        parsed = json.loads(result.output)
        jsonschema.validate(parsed, SARIF_SCHEMA)

    def test_replay_everything_html(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["replay", str(EVERYTHING_LOG), "--output", "html", "--no-llm-judges"])
        assert result.exit_code in (0, 1)
        assert "<html" in result.output
